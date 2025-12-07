import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import subprocess
import shutil
import os
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
# RSS Feed: Son eklenen bölümleri saf metin olarak verir (En garanti yol)
RSS_URL = "https://www.dizibox.live/feed/"
OUTPUT_FILE = "dizibox.m3u"
# Tarayıcıyı mobil sanmasın diye Desktop User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"

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
    options.add_argument(f"--user-agent={USER_AGENT}")
    
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

# --- STREAM ÇÖZÜCÜ ---
def resolve_stream(driver, episode_url):
    stream_url = None
    try:
        driver.get(episode_url)
        
        # 1. Iframe var mı diye bekle
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.TAG_NAME, "iframe"))
            )
        except: return None

        # 2. Sayfadaki TÜM iframe'leri tarayıp video kaynağı olanı bul
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        target_src = None
        
        for frame in iframes:
            src = frame.get_attribute("src")
            if src and ("king.php" in src or "trkv" in src or "drive" in src):
                target_src = src.replace("php?v=", "php?wmode=opaque&v=")
                break
        
        if not target_src: return None

        # 3. Player sayfasına git
        driver.get(target_src)
        time.sleep(1.5)
        
        # 4. Embed linkini bul (vidmoly/sheila)
        embed_src = None
        player_iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in player_iframes:
            src = frame.get_attribute("src")
            if src and ("vidmoly" in src or "embed" in src):
                embed_src = src
                break
        
        if not embed_src: return None
            
        if "vidmoly" in embed_src:
            embed_src = embed_src.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_src and "/sheila/" not in embed_src:
                embed_src = embed_src.replace("/embed/", "/embed/sheila/")
        
        # 5. Son durak: Şifre çözme
        driver.get(embed_src)
        time.sleep(1)
        src_code = driver.page_source
        
        # M3U8 var mı?
        if "dbx.molystream" in embed_src:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: stream_url = match.group(1)

        # Şifreli mi?
        if not stream_url:
            crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
            crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
            if crypt_data and crypt_pass:
                dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
                if dec:
                    match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                    if match: stream_url = match.group(1)

    except Exception as e:
        print(f"    ! Stream hatası: {e}")
    
    return stream_url

def extract_links_from_rss(driver):
    """RSS Beslemesinden Regex ile Link Çeker (XML Parser kullanmaz)"""
    print(f"RSS Beslemesi Okunuyor: {RSS_URL}")
    driver.get(RSS_URL)
    time.sleep(3)
    
    page_source = driver.page_source
    
    # Basit Regex: <link>http...-izle/</link> yapısını bul
    # XML yapısı bozuk olsa bile çalışır
    links = re.findall(r'<link>(https://.*?/.*?izle/)</link>', page_source)
    
    # Linkler benzersiz olsun
    unique_links = list(set(links))
    print(f"RSS Kaynağından {len(unique_links)} adet bölüm linki bulundu.")
    return unique_links

def main():
    print("DiziBox Garantili Tarayıcı Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Siteye Giriş (Cloudflare Kontrolü)
        driver.get(BASE_URL)
        time.sleep(6) # İlk açılışta uzun bekle
        
        # 2. Linkleri Topla (RSS Yöntemi)
        episode_links = extract_links_from_rss(driver)
        
        if not episode_links:
            print("RSS Boş döndü, Ana Sayfa'daki linkler Regex ile taranıyor...")
            # Yedek Plan: Ana sayfadaki tüm -izle linklerini al
            driver.get(BASE_URL)
            time.sleep(5)
            html = driver.page_source
            episode_links = list(set(re.findall(r'href=["\'](https://.*?/.*?izle/)["\']', html)))
            print(f"Ana sayfadan {len(episode_links)} link bulundu.")

        if not episode_links:
            print("❌ Kritik Hata: Hiçbir kaynaktan link bulunamadı.")
            return

        print(f"Toplam {len(episode_links)} bölüm işlenecek...")

        # 3. Her Linki İşle
        for i, url in enumerate(episode_links):
            try:
                # Linkten Başlık Üret (URL'den temizle)
                # Örn: https://.../kardeş-payı-1-sezon-1-bolum-izle/ -> Kardeş Payı 1 Sezon 1 Bolum
                slug = url.strip("/").split("/")[-1]
                title = slug.replace("-izle", "").replace("-", " ").title()
                
                print(f"[{i+1}/{len(episode_links)}] İşleniyor: {title}")
                
                stream_link = resolve_stream(driver, url)
                
                if stream_link:
                    print(f"    ✅ BAŞARILI: {stream_link[:40]}...")
                    # Poster yoksa varsayılan boş bırakıyoruz, önemli olan video
                    line = f'#EXTINF:-1 group-title="Son Eklenenler", {title}\n{stream_link}'
                    all_m3u_lines.append(line)
                    
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(all_m3u_lines))
                else:
                    print("    ❌ Video çözülemedi.")
            except Exception as e:
                print(f"    Hata: {e}")

    except Exception as e:
        print(f"Genel Çökme: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
