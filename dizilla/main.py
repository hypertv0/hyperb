import json
import base64
import time
import re
import sys
import hashlib
import requests
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
RSS_URL = "https://www.dizibox.live/feed/"
CACHE_URL = "http://webcache.googleusercontent.com/search?q=cache:https://www.dizibox.live/tum-bolumler/"
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

# --- VİDEO URL ÇIKARTMA ---
def extract_video_url(page, episode_url):
    try:
        # Bölüm sayfasına git
        page.get(episode_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title or "Access denied" in page.title:
            log("Bölüm sayfası engellendi, iframe aranıyor...", "WARNING")
            # Sayfa engellense bile bazen kaynak kodda iframe linki gizli olabilir
            # Ancak genelde erişim tam kesilir.
            return None
        
        # 1. Iframe Bul (King Player)
        iframe_src = None
        
        # CSS ile ara
        try:
            iframe_ele = page.ele("css:div#video-area iframe")
            if iframe_ele: iframe_src = iframe_ele.attr("src")
        except: pass
        
        # Bulamazsa HTML içinde regex ile ara
        if not iframe_src:
            match = re.search(r'src="([^"]*king\.php[^"]*)"', page.html)
            if match: iframe_src = match.group(1)
            
        if not iframe_src:
            log("King Player bulunamadı.", "WARNING")
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

# --- VERİ KAYNAKLARI ---

def fetch_from_rss():
    """RSS Beslemesinden veri çeker (En Hızlı ve Güvenli)"""
    log("Yöntem A: RSS Beslemesi deneniyor...", "INFO")
    try:
        # Requests ile çekmeyi dene (Hızlı)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(RSS_URL, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "xml")
            items = soup.find_all("item")
            results = []
            for item in items:
                title = item.title.text
                link = item.link.text
                # Sadece dizi bölümlerini al
                if "izle" in link or "sezon" in link:
                    results.append({"title": title, "url": link, "category": "RSS Feed"})
            return results
    except Exception as e:
        log(f"RSS Hatası: {e}", "WARNING")
    return []

def fetch_from_google_cache():
    """Google Cache üzerinden veri çeker (IP Ban Aşar)"""
    log("Yöntem B: Google Cache deneniyor...", "INFO")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(CACHE_URL, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Cache sayfasındaki linkleri bul
            # DiziBox yapısı: article.detailed-article h3 a
            articles = soup.select("article.detailed-article")
            if not articles:
                articles = soup.select("article") # Daha genel
            
            results = []
            for art in articles:
                a_tag = art.select_one("h3 a")
                if not a_tag: a_tag = art.select_one("a")
                
                if a_tag:
                    title = a_tag.text.strip()
                    href = a_tag.get("href")
                    if href and "dizibox.live" in href:
                        results.append({"title": title, "url": href, "category": "Google Cache"})
            return results
    except Exception as e:
        log(f"Cache Hatası: {e}", "WARNING")
    return []

def main():
    # 1. Veri Toplama (RSS -> Cache -> Direct)
    items = fetch_from_rss()
    
    if not items:
        log("RSS başarısız, Google Cache deneniyor...", "WARNING")
        items = fetch_from_google_cache()
        
    if not items:
        log("Google Cache başarısız, DrissionPage ile doğrudan denenecek...", "WARNING")
        # DrissionPage ile ana sayfaya gitme kodu buraya eklenebilir ama
        # önceki denemelerde başarısız olduğu için RSS/Cache'e güveniyoruz.
    
    if not items:
        log("HİÇBİR KAYNAKTAN VERİ ALINAMADI.", "CRITICAL")
        return

    log(f"Toplam {len(items)} içerik bulundu. Linkler çözülüyor...", "SUCCESS")

    # 2. Link Çözme (DrissionPage)
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    # DiziBox Cookie'leri
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
            
            for item in items[:20]: # İlk 20 bölüm
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
