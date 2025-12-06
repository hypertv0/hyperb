import json
import base64
import time
import re
import sys
import hashlib
import random
from datetime import datetime
from curl_cffi import requests as crequests # Cloudflare bypass için özel istek kütüphanesi
from DrissionPage import ChromiumPage, ChromiumOptions
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
SITEMAP_URL = "https://www.dizibox.live/post-sitemap.xml"
OUTPUT_FILE = "dizilla.m3u"

def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")
    sys.stdout.flush()

# --- CRYPTOJS ŞİFRE ÇÖZME ---
def bytes_to_key(data, salt, output=48):
    data += salt
    key = hashlib.md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = hashlib.md5(key + data).digest()
        final_key += key
    return final_key[:output]

def decrypt_cryptojs(passphrase, encrypted_base64):
    try:
        encrypted = base64.b64decode(encrypted_base64)
        salt = encrypted[8:16]
        ciphertext = encrypted[16:]
        key_iv = bytes_to_key(passphrase.encode('utf-8'), salt, 32 + 16)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return decrypted.decode('utf-8')
    except Exception as e:
        return None

# --- LİSTE OLUŞTURMA (curl_cffi ile) ---
def fetch_sitemap_links():
    log("Sitemap indiriliyor (curl_cffi)...", "INFO")
    try:
        # Gerçek bir Chrome tarayıcısı gibi davran
        session = crequests.Session(impersonate="chrome120")
        resp = session.get(SITEMAP_URL, timeout=15)
        
        if resp.status_code != 200:
            log(f"Sitemap indirilemedi. Kod: {resp.status_code}", "ERROR")
            return []
            
        # XML/HTML içeriğini parse et
        soup = BeautifulSoup(resp.content, "html.parser") # XML yerine HTML parser daha esnek
        
        links = []
        # Sitemap yapısı: <loc>URL</loc> veya <a href="URL">
        
        # Önce loc etiketlerine bak (Standart XML)
        locs = soup.find_all("loc")
        for loc in locs:
            url = loc.text.strip()
            if "-izle" in url:
                links.append(url)
                
        # Eğer loc yoksa (HTML sitemap ise), a etiketlerine bak
        if not links:
            as_tags = soup.find_all("a")
            for a in as_tags:
                href = a.get("href")
                if href and "-izle" in href:
                    links.append(href)
                    
        # Linkleri temizle ve formatla
        formatted_items = []
        for url in links:
            # Başlığı URL'den çıkar
            # .../dizi-adi-1-sezon-1-bolum-izle/
            slug = url.rstrip("/").split("/")[-1].replace("-izle", "")
            parts = slug.split("-")
            
            # Basit başlık oluşturma
            title = slug.replace("-", " ").title()
            
            # Sezon/Bölüm bulmaya çalış
            try:
                if "sezon" in parts and "bolum" in parts:
                    s_idx = parts.index("sezon")
                    b_idx = parts.index("bolum")
                    season = parts[s_idx-1]
                    episode = parts[b_idx-1]
                    # Dizi adını al
                    name_parts = parts[:s_idx-1]
                    name = " ".join(name_parts).title()
                    title = f"{name} - {season}x{episode}"
            except: pass
            
            formatted_items.append({
                "title": title,
                "url": url,
                "category": "Son Eklenenler"
            })
            
        # En yeni 30 bölümü al (Sitemap genelde eskiden yeniye veya karışıktır, ters çevirelim)
        # DiziBox sitemap'i genelde en üstte en yeniyi tutar ama emin olalım.
        return formatted_items[:30]

    except Exception as e:
        log(f"Sitemap Hatası: {e}", "CRITICAL")
        return []

# --- VİDEO URL ÇIKARTMA (DrissionPage) ---
def extract_video_url(page, episode_url):
    try:
        page.get(episode_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title:
            time.sleep(3)
        
        # 1. Iframe Bul
        iframe_src = None
        
        # CSS ile
        try:
            iframe_ele = page.ele("css:div#video-area iframe")
            if iframe_ele: iframe_src = iframe_ele.attr("src")
        except: pass
        
        # Regex ile (HTML içinde)
        if not iframe_src:
            match = re.search(r'src="([^"]*king\.php[^"]*)"', page.html)
            if match: iframe_src = match.group(1)
            
        if not iframe_src:
            log("Player bulunamadı.", "WARNING")
            return None
            
        if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
        if "king.php" in iframe_src:
            iframe_src = iframe_src.replace("king.php?v=", "king.php?wmode=opaque&v=")
            
        # 2. Player'a git
        page.get(iframe_src)
        time.sleep(1)
        
        # 3. Şifre Çözme
        html = page.html
        
        # Senaryo A: Ana Player
        match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
        if match_data:
            decrypted = decrypt_cryptojs(match_data.group(2), match_data.group(1))
            if decrypted:
                file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                if file_match: return file_match.group(1)

        # Senaryo B: İç Player (div#Player)
        nested_match = re.search(r'<div id="Player">.*?<iframe.*?src="([^"]+)".*?>', html, re.DOTALL)
        if nested_match:
            nested_src = nested_match.group(1)
            if nested_src.startswith("//"): nested_src = "https:" + nested_src
            
            page.get(nested_src)
            time.sleep(1)
            html = page.html
            
            match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
            if match_data:
                decrypted = decrypt_cryptojs(match_data.group(2), match_data.group(1))
                if decrypted:
                    file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                    if file_match: return file_match.group(1)
            
            m3u8_match = re.search(r'file:\s*"([^"]+\.m3u8[^"]*)"', html)
            if m3u8_match: return m3u8_match.group(1)

        return None

    except Exception as e:
        log(f"Hata: {e}", "ERROR")
        return None

def main():
    # 1. Linkleri Topla
    items = fetch_sitemap_links()
    
    if not items:
        log("Sitemap'ten link alınamadı. Manuel liste deneniyor...", "WARNING")
        # Fallback: Eğer sitemap çalışmazsa en azından popüler dizileri ekle
        items = [
            {"title": "The Penguin - 1x1", "url": "https://www.dizibox.live/the-penguin-1-sezon-1-bolum-izle/", "category": "Fallback"},
            {"title": "From - 3x1", "url": "https://www.dizibox.live/from-3-sezon-1-bolum-izle/", "category": "Fallback"}
        ]

    log(f"Toplam {len(items)} içerik işlenecek.", "SUCCESS")

    # 2. Videoları Çek
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    page = ChromiumPage(co)
    
    # Cookie Ekle
    page.set.cookies([
        {'name': 'LockUser', 'value': 'true', 'domain': '.dizibox.live'},
        {'name': 'isTrustedUser', 'value': 'true', 'domain': '.dizibox.live'},
        {'name': 'dbxu', 'value': str(int(time.time() * 1000)), 'domain': '.dizibox.live'}
    ])

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            for item in items:
                log(f"İşleniyor: {item['title']}", "INFO")
                stream_url = extract_video_url(page, item["url"])
                
                if stream_url:
                    ua = page.user_agent
                    final_url = stream_url
                    if ".m3u8" in stream_url:
                        final_url = f"{stream_url}|User-Agent={ua}"
                    
                    f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                    f.write(f"{final_url}\n")
                    log("Eklendi.", "SUCCESS")
                else:
                    log("Stream bulunamadı.", "WARNING")
                
                time.sleep(1)

    except Exception as e:
        log(f"Kritik Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
