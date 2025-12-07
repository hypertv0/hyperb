import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import subprocess
import shutil
import sys
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizibox.m3u"

# Her kategoriden kaç sayfa taranacak? (Tüm site için 50+ yapın ama süre yetmeyebilir)
MAX_PAGES_PER_CATEGORY = 2 

# İlk kodda olan kategoriler
CATEGORIES = [
    ("Popüler", "populer"), # Özel tip
    ("Aksiyon", "aksiyon"),
    ("Animasyon", "animasyon"),
    ("Bilim Kurgu", "bilimkurgu"),
    ("Dram", "drama"),
    ("Komedi", "komedi"),
    ("Suç", "suc")
]

# --- TARAYICI AYARLARI ---
def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Masaüstü gibi görün
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
    
    # Chrome yolunu bul
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    # Versiyonu bul (Daha önceki hatayı önlemek için)
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version = int(re.search(r'Chrome (\d+)', output.decode('utf-8')).group(1))
        driver = uc.Chrome(options=options, version_main=version)
    except:
        driver = uc.Chrome(options=options) # Otomatik
        
    return driver

# --- ŞİFRE ÇÖZME (ESKİ KODDAN ALINDI) ---
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
        
        # Cloudflare kontrolü
        if "Just a moment" in driver.title:
            WebDriverWait(driver, 20).until_not(EC.title_contains("Just a moment"))
            time.sleep(2)

        # Iframe bekle
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None

        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        
        # Player sayfasına git
        driver.get(src)
        time.sleep(1)
        
        # Embed linkini al (Vidmoly/Sheila)
        try:
            embed = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
            embed_url = embed.get_attribute("src")
        except: return None
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        # Şifre çözme sayfasına git
        driver.get(embed_url)
        time.sleep(0.5)
        src_code = driver.page_source
        
        # M3U8 Ara
        if "dbx.molystream" in embed_url:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: return match.group(1)

        # Şifreli Veri Ara
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
    print("DiziBox Full Crawler Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Giriş ve Cloudflare Geçişi
        driver.get(BASE_URL)
        print("Siteye giriliyor...")
        time.sleep(5)
        
        # Çerezleri ekle (Opsiyonel ama hızlandırır)
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

        # 2. Kategorileri Gez
        for cat_name, cat_slug in CATEGORIES:
            print(f"\n=== KATEGORİ: {cat_name} ===")
            
            for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
                # URL Oluşturma (Eski koddaki mantık)
                if cat_slug == "populer":
                    url = f"{BASE_URL}/tum-bolumler/page/{page}/?tip=populer"
                else:
                    url = f"{BASE_URL}/dizi-arsivi/page/{page}/?tur[0]={cat_slug}&yil&imdb"
                
                print(f"  Sayfa {page} taranıyor: {url}")
                driver.get(url)
                
                # Cloudflare kontrolü
                if "Just a moment" in driver.title:
                    print("  ⚠️ Cloudflare engeli, bekleniyor...")
                    try:
                        WebDriverWait(driver, 15).until_not(EC.title_contains("Just a moment"))
                        time.sleep(2)
                    except:
                        print("  ❌ Engel geçilemedi, sonraki sayfaya.")
                        continue

                # Dizi Kartlarını Bul
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                # Hem arşiv hem popüler sayfası için farklı seçiciler
                cards = soup.select("article.detailed-article") or soup.select("article.grid-box")
                
                if not cards:
                    print("  ⚠️ Dizi bulunamadı (Sayfa boş veya yapı değişmiş).")
                    break # Diğer sayfaya geçme, kategori bitmiş olabilir

                print(f"  > {len(cards)} dizi bulundu.")

                for card in cards:
                    # Dizi Linkini ve Başlığını Al
                    link_tag = card.select_one("h3 a") or card.select_one("div.post-title a") or card.select_one("a.figure-link")
                    
                    if not link_tag: continue
                    
                    series_name = link_tag.text.strip()
                    series_href = link_tag.get('href')
                    
                    # Posteri al
                    img_tag = card.select_one("img")
                    poster_url = ""
                    if img_tag:
                        poster_url = img_tag.get("data-src") or img_tag.get("src") or ""

                    print(f"    > Dizi İnceleniyor: {series_name}")
                    
                    # Dizi Sayfasına Git
                    driver.get(series_href)
                    time.sleep(2)
                    
                    # Dizi Sayfasındaki Bölümleri Bul
                    # DiziBox dizi sayfasında bölümler genelde sezonlar halindedir
                    s_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    episodes = s_soup.select("article.grid-box div.post-title a")
                    
                    # Eğer çok fazla bölüm varsa sadece son 3 tanesini al (Hız için)
                    # Hepsini isterseniz [:3] kısmını silin.
                    # episodes = episodes[:3] 
                    
                    if not episodes:
                        print("      Bölüm listesi bulunamadı.")
                        continue

                    print(f"      {len(episodes)} bölüm var. Linkler çözülüyor...")

                    for ep_link in episodes:
                        ep_title = ep_link.text.strip()
                        ep_href = ep_link.get('href')
                        full_title = f"{series_name} - {ep_title}"
                        
                        # Bölüm Linkini Çöz
                        m3u8 = resolve_stream(driver, ep_href)
                        
                        if m3u8:
                            print(f"      ✅ EKLENDİ: {ep_title}")
                            line = f'#EXTINF:-1 group-title="{cat_name}" tvg-logo="{poster_url}", {full_title}\n{m3u8}'
                            all_m3u_lines.append(line)
                            
                            # Dosyayı kaydet
                            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                                f.write("\n".join(all_m3u_lines))
                        else:
                            # print("      ❌ Link çözülemedi.")
                            pass
                            
    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
