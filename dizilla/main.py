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

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizibox.m3u"
MAX_PAGES = 3

def get_chrome_major_version():
    """Sistemdeki yüklü Chrome sürümünün ana numarasını bulur"""
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version_str = output.decode('utf-8').strip()
        print(f"Sistemdeki Chrome: {version_str}")
        match = re.search(r'Chrome (\d+)', version_str)
        if match:
            return int(match.group(1))
    except Exception as e:
        print(f"Chrome versiyonu okunamadı: {e}")
    return None

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # --- KRİTİK DÜZELTME: Chrome Yolunu Zorla Belirt ---
    # Sistemdeki 'google-chrome' komutunun nereye gittiğini buluyoruz (Bu v143 olan)
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    
    if chrome_path:
        print(f"Kullanılacak Chrome Yolu: {chrome_path}")
        options.binary_location = chrome_path
    else:
        print("UYARI: Chrome yolu bulunamadı, varsayılan kullanılacak.")

    # Versiyonu al ve eşleşen sürücüyü iste
    version = get_chrome_major_version()
    
    try:
        if version:
            print(f"ChromeDriver {version} sürümü ile başlatılıyor...")
            driver = uc.Chrome(options=options, version_main=version)
        else:
            driver = uc.Chrome(options=options)
    except Exception as e:
        print(f"Sürücü başlatma hatası: {e}")
        # Son bir deneme, versiyon belirtmeden
        print("Versiyonsuz deneniyor...")
        driver = uc.Chrome(options=options)
        
    return driver

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
        print(f"    > Link çözülüyor: {episode_url}")
        driver.get(episode_url)
        time.sleep(4) 
        
        try:
            iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
            src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        except: return None

        driver.get(src)
        time.sleep(2)
        
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
        
        if "dbx.molystream" in embed_url:
            for line in src_code.splitlines():
                if "http" in line and "m3u8" in line:
                    match = re.search(r'(https?://[^\s<"]+)', line)
                    if match: return match.group(1)

        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if match: return match.group(1)
    except Exception as e:
        print(f"    ! Hata: {e}")
    return None

def main():
    print("DiziBox Tarayıcı Başlatılıyor (Path Zorlamalı)...")
    try:
        driver = get_driver()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        exit(1)

    try:
        # Siteye Giriş (Cookie Ayarı)
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
        time.sleep(3)
        
        categories = [("Aksiyon", "aksiyon"), ("Komedi", "komedi")]
        all_m3u_lines = ["#EXTM3U"]
        
        for cat_name, cat_slug in categories:
            print(f"--- Kategori: {cat_name} ---")
            for page in range(1, MAX_PAGES + 1):
                url = f"{BASE_URL}/dizi-arsivi/page/{page}/?tur[0]={cat_slug}&yil&imdb"
                driver.get(url)
                time.sleep(3)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                articles = soup.select("article.detailed-article")
                if not articles: break
                
                for art in articles:
                    title_tag = art.select_one("h3 a")
                    if not title_tag: continue
                    series_name = title_tag.text.strip()
                    series_href = title_tag['href']
                    poster = art.select_one("img")
                    poster_url = poster.get('data-src') or poster.get('src') or ""
                    
                    print(f"  > Dizi: {series_name}")
                    driver.get(series_href)
                    time.sleep(2)
                    
                    ep_tag = BeautifulSoup(driver.page_source, 'html.parser').select_one("article.grid-box div.post-title a")
                    if ep_tag:
                        m3u_link = resolve_stream(driver, ep_tag['href'])
                        if m3u_link:
                            print(f"    + Link Bulundu: {m3u_link[:50]}...")
                            line = f'#EXTINF:-1 group-title="{cat_name}" tvg-logo="{poster_url}", {series_name} - {ep_tag.text.strip()}\n{m3u_link}'
                            all_m3u_lines.append(line)
                            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                                f.write("\n".join(all_m3u_lines))
    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("İşlem Bitti.")

if __name__ == "__main__":
    main()
