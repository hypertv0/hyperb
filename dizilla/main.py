import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import shutil
import subprocess
import os
import random
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
# Eklentideki mantık: Son bölümler sayfası
TARGET_URL = "https://www.dizibox.live/tum-bolumler/page/{}/" 
OUTPUT_FILE = "dizibox.m3u"
START_PAGE = 1
END_PAGE = 5  # Kaç sayfa taranacak?

# Eklentiden alınan kritik çerezler
COOKIES = [
    {"name": "LockUser", "value": "true", "domain": ".dizibox.live"},
    {"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"},
    {"name": "dbxu", "value": str(int(time.time() * 1000)), "domain": ".dizibox.live"}
]

def get_chrome_version():
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
    # Masaüstü User-Agent (Bot gibi görünmemek için şart)
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_version()
    try:
        driver = uc.Chrome(options=options, version_main=version) if version else uc.Chrome(options=options)
    except:
        driver = uc.Chrome(options=options)
    return driver

# --- ŞİFRE ÇÖZME ---
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
    try:
        driver.get(episode_url)
        
        # Video alanını bekle (HTML dosyasında div#video-area var)
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: 
            return None

        # Ana iframe
        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src")
        if "php?v=" in src:
            src = src.replace("php?v=", "php?wmode=opaque&v=")
            
        driver.get(src)
        time.sleep(1.5)
        
        # Player iframe (vidmoly/sheila)
        try:
            embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
            embed_url = embed.get_attribute("src")
        except: return None
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        driver.get(embed_url)
        time.sleep(1)
        src_code = driver.page_source
        
        # M3U8
        if "dbx.molystream" in embed_url:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: return match.group(1)

        # Şifreli Link
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if match: return match.group(1)

    except: pass
    return None

def main():
    print("DiziBox Sayfa Tarayıcı Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Siteye Giriş ve Çerez Basma
        print(f"Siteye giriş yapılıyor: {BASE_URL}")
        driver.get(BASE_URL)
        time.sleep(5)
        
        for cookie in COOKIES:
            try: driver.add_cookie(cookie)
            except: pass
        driver.refresh()
        time.sleep(5) # Cloudflare bekleme süresi

        # 2. Sayfaları Gez
        for page in range(START_PAGE, END_PAGE + 1):
            url = TARGET_URL.format(page)
            print(f"\n--- Sayfa {page} Taranıyor: {url} ---")
            
            driver.get(url)
            
            # İçeriğin yüklenmesini bekle (GÖNDERDİĞİN HTML YAPISINA GÖRE GÜNCELLENDİ)
            # HTML'de: <article class="post-box-grid"> var.
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article.post-box-grid"))
                )
            except:
                print("⚠️ İçerik yüklenemedi. (Cloudflare veya boş sayfa)")
                # Başlığı kontrol et
                if "Just a moment" in driver.title:
                    print("  -> Cloudflare Engeli Algılandı.")
                continue

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            # GÖNDERDİĞİN HTML'E GÖRE SEÇİCİLER:
            articles = soup.select("article.post-box-grid")
            
            print(f"  > {len(articles)} yeni bölüm bulundu.")
            
            for art in articles:
                # Link
                link_tag = art.select_one("div.box-details a")
                if not link_tag: continue
                
                ep_url = link_tag['href']
                
                # Başlık: Dizi Adı + Sezon/Bölüm
                title_strong = art.select_one("strong.archive")
                season_span = art.select_one("span.season")
                
                dizi_adi = title_strong.text.strip() if title_strong else "Bilinmeyen"
                bolum_bilgisi = season_span.text.strip() if season_span else ""
                
                full_title = f"{dizi_adi} - {bolum_bilgisi}"
                
                # Poster
                poster_div = art.select_one("div.box-image")
                poster = poster_div.get("data-src") if poster_div else ""
                
                print(f"  > İşleniyor: {full_title}")
                
                stream_link = resolve_stream(driver, ep_url)
                
                if stream_link:
                    print(f"    ✅ LİNK: {stream_link[:40]}...")
                    line = f'#EXTINF:-1 group-title="Son Eklenenler" tvg-logo="{poster}", {full_title}\n{stream_link}'
                    all_m3u_lines.append(line)
                    
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(all_m3u_lines))
                else:
                    pass
                    # print("    ❌ Stream yok.")

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
