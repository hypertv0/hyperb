import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import shutil
import subprocess
import os
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# --- KOTLIN EKLENTİSİNDEN ALINAN AYARLAR ---
BASE_URL = "https://www.dizibox.live"
TARGET_URL_TEMPLATE = "https://www.dizibox.live/tum-bolumler/page/{}/" 
OUTPUT_FILE = "dizibox.m3u"
# Eklentideki 'dbxu' değeri zaman damgasıdır, güncel tutmak için şimdikini kullanacağız
COOKIES = [
    {"name": "LockUser", "value": "true", "domain": ".dizibox.live"},
    {"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"},
    {"name": "dbxu", "value": "1744054959089", "domain": ".dizibox.live"} 
]
MAX_PAGES = 5  # Kaç sayfa taranacak? (Her sayfada yakl. 24 bölüm var)

def get_chrome_version():
    """Sistemdeki Chrome sürümünü bulur"""
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version = re.search(r'Chrome (\d+)', output.decode('utf-8')).group(1)
        return int(version)
    except:
        return None

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Kotlin eklentisi gibi davranmak için standart User-Agent
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_version()
    try:
        if version:
            driver = uc.Chrome(options=options, version_main=version)
        else:
            driver = uc.Chrome(options=options)
    except:
        driver = uc.Chrome(options=options)
    return driver

# --- EKLENTİDEKİ ŞİFRE ÇÖZME MANTIĞI (CryptoJS) ---
def bytes_to_key(data, salt, output=48):
    data = data.encode('utf-8') + salt
    key = hashlib.md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = hashlib.md5(key + data).digest()
        final_key += key
    return final_key[:output]

def decrypt_openssl(passphrase, encrypted_base64):
    try:
        encrypted_data = base64.b64decode(encrypted_base64)
        if encrypted_data[:8] != b'Salted__': return None
        salt = encrypted_data[8:16]
        cipher_bytes = encrypted_data[16:]
        key_iv = bytes_to_key(passphrase, salt, 48)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(cipher_bytes)
        return decrypted[:-decrypted[-1]].decode('utf-8')
    except: return None

def resolve_stream(driver, episode_url):
    """Kotlin dosyasındaki iframeDecode mantığının aynısı"""
    try:
        driver.get(episode_url)
        
        # 1. Video Iframe'ini Bekle
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None # Zaman aşımı

        # 2. Ana iframe linkini al ve düzelt
        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        iframe_src = iframe.get_attribute("src")
        
        # Eklentideki düzeltme: php?v= -> php?wmode=opaque&v=
        if "php?v=" in iframe_src:
            iframe_src = iframe_src.replace("php?v=", "php?wmode=opaque&v=")
            
        # 3. Player iframe'ine git (King player vb.)
        driver.get(iframe_src)
        time.sleep(1.5)
        
        # 4. İçteki embed iframe'i al (vidmoly/sheila)
        try:
            embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
            embed_url = embed.get_attribute("src")
        except: return None
            
        # Eklentideki URL düzeltmeleri
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        # 5. Son durak: Kaynağı çöz
        driver.get(embed_url)
        time.sleep(1)
        src_code = driver.page_source
        
        # A: Doğrudan M3U8 (dbx.molystream)
        if "dbx.molystream" in embed_url:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: return match.group(1)

        # B: Şifreli (CryptoJS)
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                # file: '...' yapısını bul
                match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if match: return match.group(1)
                
    except Exception as e:
        # Hata ayıklama için (gerekirse print açılabilir)
        pass
    return None

def main():
    print("DiziBox (Plugin-Based) Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Ana Sayfaya Git ve Eklenti Çerezlerini Bas
        driver.get(BASE_URL)
        time.sleep(5) # Cloudflare kontrolü için bekle
        
        for cookie in COOKIES:
            try: driver.add_cookie(cookie)
            except: pass
        
        driver.refresh()
        time.sleep(3)

        # 2. Sayfaları Gez (/tum-bolumler/page/X/)
        for page in range(1, MAX_PAGES + 1):
            url = TARGET_URL_TEMPLATE.format(page)
            print(f"\n--- Sayfa {page} Taranıyor: {url} ---")
            
            driver.get(url)
            
            # Yönlendirme Kontrolü: Eğer bizi ana sayfaya attıysa bu sayfa bozuktur veya erişim yoktur
            current_url = driver.current_url
            if current_url.rstrip('/') == BASE_URL.rstrip('/'):
                print("⚠️ Site Ana Sayfaya yönlendirdi! Erişim engellendi veya sayfa bitti.")
                # Ana sayfadaysak, bari ana sayfadaki son bölümleri alalım
                # Bu 'yedek' plandır.
            
            # İçeriğin Yüklenmesini Bekle
            try:
                # Eklentideki seçiciler: article.detailed-article
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article"))
                )
            except:
                print("⚠️ Sayfada içerik bulunamadı.")
                continue

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Eklentideki seçicileri Python'a uyarlıyoruz
            # document.select("article.detailed-article, article.article-episode-card a.figure-link")
            articles = soup.select("article.detailed-article, article.grid-box")
            
            print(f"  > {len(articles)} içerik bulundu.")

            for art in articles:
                # Linki ve Başlığı bul
                # Eklenti mantığı: h3 a -> text
                title_tag = art.select_one("h3 a") or art.select_one("div.post-title a") or art.select_one("a.figure-link")
                
                if not title_tag: continue
                
                # Eklentideki regex temizliği: -[0-9]+-.* yerine /
                # Biz doğrudan linki alıyoruz, regex'e gerek yok
                episode_url = title_tag['href']
                full_title = title_tag.text.strip()
                
                # Posteri bul
                img_tag = art.select_one("img")
                poster_url = ""
                if img_tag:
                    poster_url = img_tag.get("data-src") or img_tag.get("src") or ""

                # Eklenti türü sadece "TvSeries" olarak alıyor, biz de "Son Bölümler" diyelim
                # Veya sayfadan türü çekmeye çalışalım
                category = "Son Eklenenler"
                
                print(f"  > İşleniyor: {full_title}")
                
                # Stream linkini çöz
                stream_link = resolve_stream(driver, episode_url)
                
                if stream_link:
                    print(f"    ✅ LİNK: {stream_link[:40]}...")
                    
                    line = f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster_url}", {full_title}\n{stream_link}'
                    all_m3u_lines.append(line)
                    
                    # Dosyayı kaydet
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(all_m3u_lines))
                else:
                    # print("    ❌ Stream yok.")
                    pass

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
