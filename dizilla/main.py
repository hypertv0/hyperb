import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import json
import os
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizibox.m3u"
MAX_PAGES = 2   # Test için düşük tutun, çalışırsa artırırsınız.

def get_driver():
    """Cloudflare'i geçen özel Chrome tarayıcısı oluşturur"""
    options = uc.ChromeOptions()
    # GitHub Actions linux ortamında GUI olmadığı için bu ayarlar şart
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Headless modu bazen yakalanır, xvfb ile sanal ekran kullanacağız (YAML dosyasında)
    # options.add_argument("--headless=new") 
    
    driver = uc.Chrome(options=options, version_main=None)
    return driver

def bytes_to_key(data, salt, output=48):
    """OpenSSL Key Derivation"""
    data = data.encode('utf-8')
    data += salt
    key = hashlib.md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = hashlib.md5(key + data).digest()
        final_key += key
    return final_key[:output]

def decrypt_openssl(passphrase, encrypted_base64):
    """AES Decryption"""
    try:
        encrypted_data = base64.b64decode(encrypted_base64)
        if encrypted_data[:8] != b'Salted__':
            return None
        salt = encrypted_data[8:16]
        cipher_bytes = encrypted_data[16:]
        key_iv = bytes_to_key(passphrase, salt, 48)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(cipher_bytes)
        padding_len = decrypted[-1]
        return decrypted[:-padding_len].decode('utf-8')
    except Exception:
        return None

def resolve_stream(driver, episode_url):
    """Bölüm linkini çözer (Driver nesnesini kullanır)"""
    try:
        print(f"    > Link çözülüyor: {episode_url}")
        driver.get(episode_url)
        time.sleep(3) # Sayfanın ve JS'in yüklenmesi için bekle
        
        # 1. Iframe bul
        try:
            iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
            iframe_src = iframe.get_attribute("src").replace("php?v=", "php?wmode=opaque&v=")
        except:
            return None

        # 2. Player Iframe'e git
        driver.get(iframe_src)
        time.sleep(2)
        
        try:
            embed_iframe = driver.find_element(By.CSS_SELECTOR, "div#Player iframe")
            embed_url = embed_iframe.get_attribute("src")
        except:
            return None
            
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        # 3. Decryption Sayfası
        driver.get(embed_url)
        time.sleep(1)
        content = driver.page_source
        
        # Direkt M3U8
        if "dbx.molystream" in embed_url:
            for line in content.splitlines():
                if "http" in line and ".m3u8" in line: # HTML taglerini temizle
                    clean_link = re.search(r'(https?://[^\s<"]+)', line)
                    if clean_link: return clean_link.group(1)

        # Şifreli
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', content)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', content)
        
        if crypt_data and crypt_pass:
            decrypted = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if decrypted:
                match = re.search(r"file:\s*'(.*?)'", decrypted) or re.search(r'file:\s*"(.*?)"', decrypted)
                if match: return match.group(1)
                
    except Exception as e:
        print(f"    ! Hata: {e}")
    return None

def main():
    print("DiziBox Tarayıcı Başlatılıyor (Selenium Mode)...")
    
    driver = get_driver()
    
    # DiziBox'a ilk giriş ve cookie ayarı
    try:
        driver.get(BASE_URL)
        time.sleep(5) # Cloudflare kontrolünü geçmesi için bekle
        
        # Cookie ekle (Dizibox kodundaki trusted cookie'ler)
        driver.add_cookie({"name": "LockUser", "value": "true", "domain": ".dizibox.live"})
        driver.add_cookie({"name": "isTrustedUser", "value": "true", "domain": ".dizibox.live"})
        driver.add_cookie({"name": "dbxu", "value": "1744054959089", "domain": ".dizibox.live"})
        driver.refresh()
        time.sleep(3)
        
    except Exception as e:
        print("Siteye erişilemedi:", e)
        driver.quit()
        return

    categories = [
        ("Aksiyon", "aksiyon"),
        # ("Komedi", "komedi"), # Test için kapalı, açabilirsiniz
    ]
    
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        for cat_name, cat_slug in categories:
            print(f"--- Kategori: {cat_name} ---")
            for page in range(1, MAX_PAGES + 1):
                url = f"{BASE_URL}/dizi-arsivi/page/{page}/?tur[0]={cat_slug}&yil&imdb"
                print(f"Sayfa Taranıyor: {page}")
                
                driver.get(url)
                time.sleep(3)
                
                if "Just a moment" in driver.title or "Access denied" in driver.title:
                    print("Cloudflare Engelini Geçemedik! Sayfa atlanıyor.")
                    continue

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                articles = soup.select("article.detailed-article")
                
                if not articles:
                    print("Bu sayfada içerik yok.")
                    break
                
                for art in articles:
                    title_tag = art.select_one("h3 a")
                    img_tag = art.select_one("img")
                    if not title_tag: continue
                    
                    series_name = title_tag.text.strip()
                    series_href = title_tag['href']
                    poster = img_tag.get('data-src') or img_tag.get('src') or ""
                    
                    # Dizi detayına gitmeden hızlıca listeyi almayı dene (Varsa)
                    # Yoksa dizi içine girip son bölümü alacağız
                    print(f"  > Dizi: {series_name}")
                    
                    # Dizi sayfasına git
                    driver.get(series_href)
                    time.sleep(2)
                    s_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    
                    # Son eklenen bölümü al (Sadece en üstteki 1 tanesi, hız için)
                    first_ep = s_soup.select_one("article.grid-box div.post-title a")
                    
                    if first_ep:
                        ep_title = first_ep.text.strip()
                        ep_href = first_ep['href']
                        
                        stream_url = resolve_stream(driver, ep_href)
                        if stream_url:
                            line = f'#EXTINF:-1 group-title="{cat_name}" tvg-logo="{poster}", {series_name} - {ep_title}\n{stream_url}'
                            all_m3u_lines.append(line)
                            
                            # Her başarılı linkte dosyayı güncelle
                            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                                f.write("\n".join(all_m3u_lines))
    finally:
        driver.quit()
        print("Tarama bitti.")

if __name__ == "__main__":
    main()
