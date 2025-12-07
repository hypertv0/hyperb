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
# Sitenin tüm dizilerini barındıran harita
SITEMAP_URL = "https://www.dizibox.live/tvseries-sitemap.xml" 
OUTPUT_FILE = "dizibox.m3u"

# DİKKAT: Tüm siteyi çekmek günler sürer. 
# Test için bu sayıyı düşük tutun (Örn: 5). Sınırsız için: 99999
MAX_SERIES_LIMIT = 5 

# Her diziden kaç bölüm çekilsin? (0 = Hepsi)
# Hız için '1' yaparsanız sadece son bölümü alır.
MAX_EPISODES_PER_SERIES = 1 

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
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_major_version()
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

# --- STREAM ÇÖZÜCÜ ---
def resolve_stream(driver, episode_url):
    try:
        driver.get(episode_url)
        
        # Video iframe'ini bekle
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None

        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        
        driver.get(src)
        time.sleep(1)
        
        # Player (Sheila/Vidmoly)
        try:
            embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
            embed_url = embed.get_attribute("src")
        except: return None
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        driver.get(embed_url)
        time.sleep(0.5)
        src_code = driver.page_source
        
        # M3U8 Ara
        if "dbx.molystream" in embed_url:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: return match.group(1)

        # Şifreli Ara
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if match: return match.group(1)
    except: pass
    return None

def get_all_series_from_sitemap(driver):
    """Sitemap üzerinden TÜM dizi linklerini çeker"""
    print(f"Site Haritası Okunuyor: {SITEMAP_URL}")
    driver.get(SITEMAP_URL)
    time.sleep(5)
    
    # Sayfa kaynağındaki tüm URL'leri al (Regex ile)
    # XML parse hatası almamak için metin olarak tarıyoruz
    html = driver.page_source
    # <loc>https://www.dizibox.live/diziler/dizi-adi/</loc> yapısını bul
    urls = re.findall(r'<loc>(https://www.dizibox.live/diziler/[^<]+)</loc>', html)
    
    # Tekrarları temizle
    unique_urls = list(set(urls))
    print(f"Haritada {len(unique_urls)} adet dizi bulundu.")
    return unique_urls

def scrape_series(driver, series_url, all_m3u_lines):
    """Bir dizinin içine girer, bilgileri alır ve bölümleri tarar"""
    try:
        driver.get(series_url)
        
        # Sayfanın yüklenmesini bekle
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.post-title"))
            )
        except:
            # print("  ⚠️ Dizi sayfası yüklenemedi.")
            return

        # Metadata Çek (Sayfa kaynağından Regex veya CSS ile)
        # Hız için JavaScript execute edebiliriz veya CSS
        info = driver.execute_script("""
            var title = document.querySelector('h1.post-title')?.innerText || 'Bilinmeyen Dizi';
            var poster = document.querySelector('div.tv-poster img')?.getAttribute('src') || '';
            var genre = document.querySelector('div.tv-poster-meta a')?.innerText || 'Genel';
            // Bölüm linklerini topla (Sezonlar genelde gizlidir ama linkler HTML'dedir)
            var links = [];
            document.querySelectorAll('article.grid-box div.post-title a').forEach(a => {
                links.push({text: a.innerText, href: a.href});
            });
            return {title: title, poster: poster, genre: genre, episodes: links};
        """)
        
        series_title = info['title']
        poster_url = info['poster']
        category = info['genre']
        episodes = info['episodes']
        
        if not episodes:
            return

        print(f"  > {series_title} ({len(episodes)} bölüm) - Kategori: {category}")
        
        # Limit kontrolü (Tüm bölümler mi, son bölüm mü?)
        if MAX_EPISODES_PER_SERIES > 0:
            episodes = episodes[:MAX_EPISODES_PER_SERIES]

        for ep in episodes:
            ep_title = ep['text']
            ep_href = ep['href']
            full_title = f"{series_title} - {ep_title}"
            
            # print(f"    Link Çözülüyor: {ep_title}")
            m3u8 = resolve_stream(driver, ep_href)
            
            if m3u8:
                print(f"    ✅ EKLENDİ: {ep_title}")
                line = f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster_url}", {full_title}\n{m3u8}'
                all_m3u_lines.append(line)
                
                # Anlık Kayıt
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write("\n".join(all_m3u_lines))
            else:
                pass
                # print("    ❌ Çözülemedi")

    except Exception as e:
        print(f"  Dizi hatası: {e}")

def main():
    print("DiziBox Tam Arşiv Tarayıcı (Sitemap Modu)...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Siteye Isınma Turu
        driver.get(BASE_URL)
        time.sleep(5)
        # Çerez
        driver.add_cookie({"name": "LockUser", "value": "true", "domain": ".dizibox.live"})
        driver.add_cookie({"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"})
        
        # 2. Tüm Dizileri Al
        series_list = get_all_series_from_sitemap(driver)
        
        if not series_list:
            print("❌ Dizi listesi alınamadı! Site haritası engellenmiş olabilir.")
            # Yedek: Ana sayfa linklerini topla
            driver.get(BASE_URL)
            html = driver.page_source
            series_list = list(set(re.findall(r'href="(https://www.dizibox.live/diziler/[^"]+)"', html)))
            print(f"Yedek: Ana sayfadan {len(series_list)} dizi bulundu.")

        # Limit uygula (Tüm siteyi taramak istemiyorsanız)
        if MAX_SERIES_LIMIT > 0:
            series_list = series_list[:MAX_SERIES_LIMIT]
            
        print(f"Toplam {len(series_list)} dizi işlenecek.")
        
        # 3. Sırayla işle
        for i, url in enumerate(series_list):
            print(f"[{i+1}/{len(series_list)}] İşleniyor...")
            scrape_series(driver, url, all_m3u_lines)
            
    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
