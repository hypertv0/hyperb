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
OUTPUT_FILE = "dizibox.m3u"
MAX_PAGES = 3

def get_chrome_major_version():
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version_str = output.decode('utf-8').strip()
        print(f"Sistemdeki Chrome: {version_str}")
        match = re.search(r'Chrome (\d+)', version_str)
        if match: return int(match.group(1))
    except: pass
    return None

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Chrome yolunu zorla
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_major_version()
    try:
        if version:
            driver = uc.Chrome(options=options, version_main=version)
        else:
            driver = uc.Chrome(options=options)
    except Exception as e:
        print(f"Driver Hata: {e}, versiyonsuz deneniyor...")
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
        print(f"    > Link çözülüyor: {episode_url}")
        driver.get(episode_url)
        
        # Video alanının yüklenmesini bekle
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except:
            print("    ! Iframe bulunamadı (Zaman aşımı)")
            return None

        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        
        driver.get(src)
        time.sleep(2) # Player yüklenmesi için kısa bekleme
        
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
        
        # M3U8 Kontrol
        if "dbx.molystream" in embed_url:
            for line in src_code.splitlines():
                if "http" in line and "m3u8" in line:
                    match = re.search(r'(https?://[^\s<"]+)', line)
                    if match: return match.group(1)

        # AES Şifre Kontrol
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

def check_cloudflare(driver):
    """Cloudflare kontrolü ve bekleme"""
    title = driver.title
    if "Just a moment" in title or "Cloudflare" in title or "Attention Required" in title:
        print(f"⚠️ Cloudflare tespit edildi ({title}). Bekleniyor...")
        try:
            # 20 saniyeye kadar sayfanın başlığının değişmesini bekle
            WebDriverWait(driver, 20).until_not(EC.title_contains("Just a moment"))
            print("✅ Cloudflare geçildi!")
            time.sleep(2) # Garanti olsun diye biraz daha bekle
        except:
            print("❌ Cloudflare geçilemedi!")
            return False
    return True

def main():
    print("DiziBox Tarayıcı Başlatılıyor (Gelişmiş Bekleme Modu)...")
    try:
        driver = get_driver()
    except Exception as e:
        print(f"CRITICAL: {e}")
        exit(1)

    try:
        driver.get(BASE_URL)
        check_cloudflare(driver)
        
        # Çerezleri ekle
        cookies = [
            {"name": "LockUser", "value": "true", "domain": ".dizibox.live"},
            {"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"},
            {"name": "dbxu", "value": "1744054959089", "domain": ".dizibox.live"}
        ]
        for c in cookies: 
            try: driver.add_cookie(c)
            except: pass
        driver.refresh()
        check_cloudflare(driver)
        
        categories = [("Aksiyon", "aksiyon"), ("Komedi", "komedi"), ("Bilim Kurgu", "bilimkurgu")]
        all_m3u_lines = ["#EXTM3U"]
        
        for cat_name, cat_slug in categories:
            print(f"\n--- Kategori: {cat_name} ---")
            for page in range(1, MAX_PAGES + 1):
                url = f"{BASE_URL}/dizi-arsivi/page/{page}/?tur[0]={cat_slug}&yil&imdb"
                print(f"Sayfa {page} yükleniyor: {url}")
                
                driver.get(url)
                if not check_cloudflare(driver):
                    print("Sayfa atlanıyor (Cloudflare).")
                    continue
                
                # İçeriğin yüklenmesini bekle (article etiketini bekle)
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "article.detailed-article"))
                    )
                except:
                    print("⚠️ İçerik bulunamadı veya süre doldu. Sayfa Başlığı:", driver.title)
                    # Eğer içerik yoksa bu kategori bitmiş olabilir
                    break

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                articles = soup.select("article.detailed-article")
                
                if not articles:
                    print("Makale listesi boş.")
                    break
                
                print(f"  > {len(articles)} dizi bulundu.")
                
                for art in articles:
                    title_tag = art.select_one("h3 a")
                    if not title_tag: continue
                    series_name = title_tag.text.strip()
                    series_href = title_tag['href']
                    poster = art.select_one("img")
                    poster_url = poster.get('data-src') or poster.get('src') or ""
                    
                    print(f"  > Dizi İnceleniyor: {series_name}")
                    driver.get(series_href)
                    check_cloudflare(driver)
                    
                    # Son bölümü bul
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "article.grid-box"))
                        )
                        ep_tag = BeautifulSoup(driver.page_source, 'html.parser').select_one("article.grid-box div.post-title a")
                        
                        if ep_tag:
                            m3u_link = resolve_stream(driver, ep_tag['href'])
                            if m3u_link:
                                print(f"    ✅ LİNK: {m3u_link[:40]}...")
                                line = f'#EXTINF:-1 group-title="{cat_name}" tvg-logo="{poster_url}", {series_name} - {ep_tag.text.strip()}\n{m3u_link}'
                                all_m3u_lines.append(line)
                                # Dosyayı anlık kaydet
                                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                                    f.write("\n".join(all_m3u_lines))
                    except:
                        print("    ! Bölüm listesi yüklenemedi.")

    except Exception as e:
        print(f"Genel Beklenmedik Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
