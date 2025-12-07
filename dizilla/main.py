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
# Dizilerin listesinin olduğu XML (Genelde engellenmez)
SITEMAP_URL = "https://www.dizibox.live/tvseries-sitemap.xml"
OUTPUT_FILE = "dizibox.m3u"
MAX_SERIES_TO_CHECK = 1000 # Kaç dizi taransın? (Hız için limit koyabilirsiniz)

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

# --- BÖLÜM ÇÖZÜCÜ ---
def resolve_episode_stream(driver, episode_url):
    stream_url = None
    try:
        driver.get(episode_url)
        # Video iframe'ini bekle
        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None

        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        
        driver.get(src)
        time.sleep(1)
        
        embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
        embed_url = embed.get_attribute("src")
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        driver.get(embed_url)
        time.sleep(1)
        src_code = driver.page_source
        
        # M3U8 veya Şifreli Link
        if "dbx.molystream" in embed_url:
            for line in src_code.splitlines():
                if "http" in line and "m3u8" in line:
                    match = re.search(r'(https?://[^\s<"]+)', line)
                    if match: stream_url = match.group(1)

        if not stream_url:
            crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
            crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
            if crypt_data and crypt_pass:
                dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
                if dec:
                    match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                    if match: stream_url = match.group(1)
                    
    except Exception as e:
        print(f"    ! Hata: {e}")
    
    return stream_url

def get_series_list(driver):
    """Sitemap veya Ana Sayfadan Dizi Linklerini Toplar"""
    series_urls = []
    
    # 1. Yöntem: Sitemap (En Temizi)
    print("--- 1. Yöntem: Sitemap Taranıyor ---")
    try:
        driver.get(SITEMAP_URL)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'xml') # XML parser
        locs = soup.find_all("loc")
        if not locs: # Eğer XML çalışmazsa HTML olarak dene
             soup = BeautifulSoup(driver.page_source, 'html.parser')
             locs = soup.find_all("loc")
             
        for loc in locs:
            url = loc.text.strip()
            if "/diziler/" in url:
                series_urls.append(url)
        
        print(f"Sitemap'ten {len(series_urls)} dizi bulundu.")
    except Exception as e:
        print(f"Sitemap hatası: {e}")

    # 2. Yöntem: Sitemap boşsa Ana Sayfa'yı tara (Yedek Plan)
    if len(series_urls) < 5:
        print("--- 2. Yöntem: Ana Sayfa Taranıyor ---")
        driver.get(BASE_URL)
        time.sleep(5)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        links = soup.find_all('a', href=True)
        for link in links:
            href = link['href']
            if "/diziler/" in href and href not in series_urls:
                series_urls.append(href)
        print(f"Ana sayfadan {len(series_urls)} dizi eklendi.")
    
    # Listeyi karıştır veya ters çevir ki hep aynıları gelmesin (İsteğe bağlı)
    # series_urls.reverse() 
    return series_urls[:MAX_SERIES_TO_CHECK]

def main():
    print("DiziBox Akıllı Tarayıcı Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # Önce Dizi Listesini Oluştur
        series_list = get_series_list(driver)
        
        if not series_list:
            print("❌ Hiç dizi bulunamadı! Site tamamen engelliyor olabilir.")
            return

        print(f"Toplam {len(series_list)} dizi işlenecek.")

        for i, series_url in enumerate(series_list):
            print(f"[{i+1}/{len(series_list)}] Diziye Gidiliyor: {series_url}")
            
            try:
                driver.get(series_url)
                # Dizi sayfasının yüklenmesini bekle
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "article.grid-box, img.wp-post-image"))
                    )
                except:
                    print("  ⚠️ Sayfa yüklenemedi veya boş.")
                    continue

                # Başlık ve Poster
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                title_h1 = soup.select_one("h1.post-title") 
                series_name = title_h1.text.strip() if title_h1 else "Bilinmeyen Dizi"
                
                poster_tag = soup.select_one("div.tv-poster img") or soup.select_one("img.wp-post-image")
                poster_url = ""
                if poster_tag:
                    poster_url = poster_tag.get("data-src") or poster_tag.get("src") or ""
                
                # Tür (Kategori)
                cat_tag = soup.select_one("div.tv-poster-meta a") or soup.select_one("a[href*='/tur/']")
                category = cat_tag.text.strip() if cat_tag else "Genel"

                # En Son Bölümü Bul (Genelde listenin en üstündeki grid-box)
                # Dizi sayfasında bölümler "article.grid-box" içindedir
                episodes = soup.select("article.grid-box div.post-title a")
                
                if not episodes:
                    # Alternatif yapı: Tablo veya liste olabilir
                    episodes = soup.select("li.season-episode a") 
                
                if episodes:
                    # En üstteki bölüm (Son Bölüm)
                    last_ep_link = episodes[0]
                    ep_title = last_ep_link.text.strip()
                    ep_href = last_ep_link['href']
                    
                    full_title = f"{series_name} - {ep_title}"
                    print(f"  > Son Bölüm İnceleniyor: {ep_title}")
                    
                    stream_link = resolve_episode_stream(driver, ep_href)
                    
                    if stream_link:
                        print(f"    ✅ LİNK: {stream_link[:40]}...")
                        line = f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster_url}", {full_title}\n{stream_link}'
                        all_m3u_lines.append(line)
                        
                        # Dosyayı her başarılı işlemde güncelle
                        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                            f.write("\n".join(all_m3u_lines))
                    else:
                        print("    ❌ Stream çözülemedi.")
                else:
                    print("  ⚠️ Bölüm bulunamadı.")
            
            except Exception as e:
                print(f"  Dizi işleme hatası: {e}")

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
