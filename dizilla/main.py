import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import shutil
import subprocess
import os
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizibox.m3u"
# Hız için limit (0 = Sınırsız, 5 = Her listeden 5er tane alır test için)
# Tüm siteyi çekmek istiyorsan burayı 0 yap veya çok yüksek bir sayı ver.
TEST_LIMIT = 0 

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
    # HTML dosyasındaki mobil görünümü simüle etmek için aynı User-Agent'ı kullanıyoruz
    options.add_argument("--user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_version()
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

def resolve_stream(driver, episode_url):
    """Verilen bölüm linkine gider ve videoyu çözer"""
    try:
        driver.get(episode_url)
        
        # Iframe'in yüklenmesini bekle (HTML yapısında div#video-area var)
        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#video-area iframe"))
            )
        except: return None

        iframe = driver.find_element(By.CSS_SELECTOR, "div#video-area iframe")
        src = iframe.get_attribute("src")
        
        # Link düzeltme
        if "php?v=" in src:
            src = src.replace("php?v=", "php?wmode=opaque&v=")
            
        driver.get(src)
        time.sleep(1.5)
        
        # Player Iframe (Vidmoly/Sheila)
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
        
        # M3U8 veya Şifre
        if "dbx.molystream" in embed_url:
            match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
            if match: return match.group(1)

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
    print("DiziBox HTML Analiz Modu Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Ana Sayfayı Aç (Tüm içerik burada gizli)
        print(f"Ana sayfaya gidiliyor: {BASE_URL}")
        driver.get(BASE_URL)
        time.sleep(7) # Sayfanın ve overlay'in yüklenmesi için bekle
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # LİSTE 1: YENİ EKLENEN BÖLÜMLER (HTML: section#recent-posts)
        print("\n--- 1. Yeni Eklenen Bölümler Taranıyor ---")
        recent_posts = soup.select("section#recent-posts article.post-box-grid")
        print(f"Bulunan yeni bölüm sayısı: {len(recent_posts)}")
        
        process_list = []
        
        for post in recent_posts:
            link_tag = post.select_one("div.box-details a")
            if not link_tag: continue
            
            href = link_tag.get('href')
            title_tag = post.select_one("strong.archive")
            ep_tag = post.select_one("span.season")
            
            title = title_tag.text.strip() if title_tag else "Bilinmeyen Dizi"
            episode = ep_tag.text.strip() if ep_tag else ""
            full_title = f"{title} - {episode}"
            img_tag = post.select_one("div.box-image")
            poster = img_tag.get('data-src') if img_tag else ""
            
            process_list.append({
                "title": full_title,
                "url": href,
                "poster": poster,
                "category": "Yeni Eklenenler"
            })

        # LİSTE 2: TÜM DİZİLER LİSTESİ (HTML: ul#all-tv-series-list)
        print("\n--- 2. Tüm Dizi Listesi Taranıyor ---")
        all_series_list = soup.select("ul#all-tv-series-list li.search-target a")
        print(f"Bulunan toplam dizi sayısı: {len(all_series_list)}")
        
        for item in all_series_list:
            href = item.get('href')
            name = item.text.strip()
            img_tag = item.select_one("img")
            poster = img_tag.get('data-src') if img_tag else ""
            
            # Dizinin kendisine gidip son bölümü alacağız
            # Şimdilik listeye ekle, aşağıda işlenecek
            process_list.append({
                "title": name,
                "url": href,
                "poster": poster,
                "category": "Arşiv",
                "is_series_page": True # Bu bir dizi sayfası, bölüm değil
            })

        # --- LİNKLERİ İŞLEME ---
        if TEST_LIMIT > 0:
            process_list = process_list[:TEST_LIMIT]
            
        print(f"\nToplam işlenecek öğe: {len(process_list)}")
        
        for i, item in enumerate(process_list):
            print(f"[{i+1}/{len(process_list)}] İşleniyor: {item['title']}")
            
            target_url = item['url']
            
            # Eğer bu bir dizi sayfasıysa, içine girip son bölümü bulmalıyız
            if item.get("is_series_page"):
                try:
                    driver.get(target_url)
                    time.sleep(2)
                    # Dizi sayfasında son bölümü bul (genelde en üstteki article.grid-box)
                    series_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    last_ep = series_soup.select_one("article.grid-box div.post-title a")
                    if last_ep:
                        target_url = last_ep.get('href')
                        item['title'] += " - " + last_ep.text.strip()
                    else:
                        print("  > Bölüm bulunamadı, geçiliyor.")
                        continue
                except:
                    continue

            # Stream linkini çöz
            stream_link = resolve_stream(driver, target_url)
            
            if stream_link:
                print(f"  ✅ LİNK: {stream_link[:40]}...")
                line = f'#EXTINF:-1 group-title="{item["category"]}" tvg-logo="{item["poster"]}", {item["title"]}\n{stream_link}'
                all_m3u_lines.append(line)
                
                # Dosyaya yaz
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write("\n".join(all_m3u_lines))
            else:
                pass
                # print("  ❌ Stream yok.")

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
