import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import subprocess
import shutil
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
TARGET_URL = "https://www.dizibox.live/tum-bolumler/page/{}/"
OUTPUT_FILE = "dizibox.m3u"
MAX_PAGES = 3

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
    # MOBİL GÖRÜNÜMDEN KURTULMAK İÇİN MASAÜSTÜ USER-AGENT
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
    
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

# --- STREAM VE DETAY ÇÖZÜCÜ ---
def resolve_episode_details(driver, episode_url):
    details = {"stream": None, "category": "Genel", "poster": ""}
    
    try:
        driver.get(episode_url)
        
        # Video alanı yüklenene kadar bekle
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Poster (Lazy load kontrolü)
        poster_tag = soup.select_one("div.tv-poster img") or soup.select_one("img.wp-post-image") or soup.select_one("img.poster")
        if poster_tag:
            details["poster"] = poster_tag.get("data-src") or poster_tag.get("src") or ""

        # Kategori
        cat_tags = soup.select("a[href*='/tur/']")
        if cat_tags:
            details["category"] = cat_tags[0].text.strip()

        # Video Linki
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
    print("DiziBox Tarayıcı (Regex Modu) Başlatılıyor...")
    driver = get_driver()
    
    all_m3u_lines = ["#EXTM3U"]

    try:
        # Ana Sayfaya Git ve Çerezleri Bas
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

        for page in range(1, MAX_PAGES + 1):
            url = TARGET_URL.format(page)
            print(f"\n--- Sayfa {page} Taranıyor: {url} ---")
            
            driver.get(url)
            time.sleep(4) # Sayfanın iyice yüklenmesini bekle
            
            # BeautifulSoup ile tüm linkleri çek
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            all_links = soup.find_all('a', href=True)
            
            episode_list = []
            seen_urls = set()
            
            print(f"  > Sayfada toplam {len(all_links)} link bulundu. Filtreleniyor...")
            
            # --- ÖNEMLİ KISIM: REGEX İLE LİNK BULMA ---
            # CSS Class'ına bakmaksızın, URL yapısında "sezon" ve "bolum" geçenleri alıyoruz.
            for link in all_links:
                href = link['href']
                # DiziBox URL formatı: .../dizi-adi-1-sezon-1-bolum-izle/
                if "sezon" in href and "bolum" in href and "diziler" not in href:
                    if href not in seen_urls:
                        title = link.get_text(strip=True)
                        if not title: # Text yoksa title attribute dene
                            title = link.get('title', '').strip()
                        if not title: # Hala yoksa URL'den üret
                            title = href.split('/')[-2].replace('-', ' ').title()
                        
                        episode_list.append((href, title))
                        seen_urls.add(href)

            print(f"  > {len(episode_list)} adet izlenebilir bölüm tespit edildi.")
            
            if not episode_list:
                print("  ⚠️ Bölüm bulunamadı! Sayfa yapısı veya Cloudflare engeli olabilir.")
                print(f"  Görülen Sayfa Başlığı: {driver.title}")
                continue

            for href, title in episode_list:
                print(f"  > İşleniyor: {title}")
                
                details = resolve_episode_details(driver, href)
                
                if details and details["stream"]:
                    category = details["category"]
                    poster = details["poster"]
                    clean_title = title.replace("\n", " ").strip()
                    
                    print(f"    ✅ LİNK: {clean_title} ({category})")
                    
                    line = f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster}", {clean_title}\n{details["stream"]}'
                    all_m3u_lines.append(line)
                    
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(all_m3u_lines))
                else:
                    print("    ❌ Stream yok.")

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
