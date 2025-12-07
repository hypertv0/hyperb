import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import subprocess
import shutil
import os
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
# Güvenilir Kaynak: Son Eklenen Bölümler Sayfası
TARGET_URL = "https://www.dizibox.live/tum-bolumler/page/{}/"
OUTPUT_FILE = "dizibox.m3u"
MAX_PAGES = 3  # Her gün son 3 sayfayı (yaklaşık 60-90 bölüm) tarar

def get_chrome_major_version():
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version_str = output.decode('utf-8').strip()
        match = re.search(r'Chrome (\d+)', version_str)
        if match: return int(match.group(1))
    except: pass
    return None

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_major_version()
    try:
        if version:
            driver = uc.Chrome(options=options, version_main=version)
        else:
            driver = uc.Chrome(options=options)
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

# --- STREAM VE KATEGORİ ÇÖZÜCÜ ---
def resolve_episode_details(driver, episode_url):
    """Hem video linkini hem de kategoriyi çeker"""
    details = {"stream": None, "category": "Genel", "poster": ""}
    
    try:
        driver.get(episode_url)
        
        # Cloudflare bekleme
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except:
            return None

        # 1. Kategoriyi Bul (Breadcrumb veya Etiketlerden)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Poster bul
        poster_tag = soup.select_one("div.tv-poster img") or soup.select_one("img.wp-post-image")
        if poster_tag:
            details["poster"] = poster_tag.get("src") or poster_tag.get("data-src") or ""

        # Kategori bul (Genelde 'tur' linkleri)
        cat_tags = soup.select("a[href*='/tur/']")
        if cat_tags:
            # İlk bulunan türü al (Örn: Aksiyon)
            details["category"] = cat_tags[0].text.strip()

        # 2. Video Linkini Çöz
        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        
        driver.get(src)
        time.sleep(1.5)
        
        embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
        embed_url = embed.get_attribute("src")
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        driver.get(embed_url)
        time.sleep(1)
        src_code = driver.page_source
        
        if "dbx.molystream" in embed_url:
            for line in src_code.splitlines():
                if "http" in line and "m3u8" in line:
                    match = re.search(r'(https?://[^\s<"]+)', line)
                    if match: details["stream"] = match.group(1)

        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if match: details["stream"] = match.group(1)
                
    except Exception as e:
        print(f"    ! Detay hatası: {e}")
    
    return details

def main():
    print("DiziBox Tarayıcı Başlatılıyor (Tüm Bölümler Modu)...")
    driver = get_driver()
    
    # M3U Dosyasını sıfırla veya oku (Burada sıfırlıyoruz)
    all_m3u_lines = ["#EXTM3U"]

    try:
        # Siteye bir kere girip çerezleri ayarla
        driver.get(BASE_URL)
        time.sleep(5)
        cookies = [
            {"name": "LockUser", "value": "true", "domain": ".dizibox.live"},
            {"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"},
            {"name": "dbxu", "value": "1744054959089", "domain": ".dizibox.live"}
        ]
        for c in cookies: 
            try: driver.add_cookie(c)
            except: pass
        driver.refresh()

        # Sayfaları Tara
        for page in range(1, MAX_PAGES + 1):
            url = TARGET_URL.format(page)
            print(f"\n--- Sayfa {page} Taranıyor: {url} ---")
            
            driver.get(url)
            time.sleep(3)
            
            # İçeriğin yüklenmesini bekle
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "article"))
                )
            except:
                print("⚠️ İçerik yüklenemedi, diğer sayfaya geçiliyor.")
                continue

            # Sayfadaki tüm bölüm linklerini bul (Href içinde '-izle' geçenler)
            # Bu en güvenli yöntemdir çünkü CSS classları değişebilir ama link yapısı zor değişir.
            links = driver.find_elements(By.XPATH, "//article//a[contains(@href, '-izle')]")
            
            # Linkleri ve başlıkları topla (Stale element hatasını önlemek için önce listeye al)
            episode_list = []
            for link in links:
                try:
                    href = link.get_attribute("href")
                    # Başlık genellikle linkin içindeki text veya title attribute'dur
                    title = link.text.strip()
                    if not title:
                        title = link.get_attribute("title")
                    
                    if href and href not in [x[0] for x in episode_list]:
                        episode_list.append((href, title))
                except: pass
            
            print(f"  > {len(episode_list)} bölüm bulundu.")
            
            # Bulunan bölümleri tek tek gez
            for href, title in episode_list:
                print(f"  > İşleniyor: {title}")
                
                details = resolve_episode_details(driver, href)
                
                if details and details["stream"]:
                    category = details["category"]
                    poster = details["poster"]
                    
                    # Başlığı temizle (Varsa gereksiz boşlukları at)
                    clean_title = title.replace("\n", " ").strip()
                    if not clean_title: clean_title = "Bilinmeyen Bolum"
                    
                    print(f"    ✅ LİNK ALINDI ({category}): {clean_title}")
                    
                    line = f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster}", {clean_title}\n{details["stream"]}'
                    all_m3u_lines.append(line)
                    
                    # Her başarılı işlemde dosyayı kaydet
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(all_m3u_lines))
                else:
                    print("    ❌ Stream bulunamadı.")
                
                # Cloudflare'i kızdırmamak için kısa bekleme
                # time.sleep(1)

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
