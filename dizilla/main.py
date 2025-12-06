import os
import json
import base64
import re
import time
from datetime import datetime
from curl_cffi import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://dizilla40.com"
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
AES_IV = bytes([0] * 16)
OUTPUT_FILE = "dizilla.m3u"

def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")

# --- ŞİFRE ÇÖZME ---
def decrypt_dizilla_response(encrypted_data):
    try:
        if not encrypted_data: return None
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except:
        return None

# --- OTURUM AÇMA ---
# Cloudflare WARP arkasında olacağımız için Chrome taklidi yeterli olacaktır.
session = requests.Session(impersonate="chrome120")
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Origin": BASE_URL,
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
})

def extract_contentx(url, referer):
    try:
        resp = session.get(url, headers={"Referer": referer}, timeout=10)
        match = re.search(r"window\.openPlayer\('([^']+)'", resp.text)
        if match:
            extract_id = match.group(1)
            source_url = f"https://contentx.me/source2.php?v={extract_id}"
            vid_resp = session.get(source_url, headers={"Referer": url}, timeout=10)
            vid_match = re.search(r'file":"([^"]+)"', vid_resp.text)
            if vid_match:
                return vid_match.group(1).replace("\\", "")
    except Exception as e:
        log(f"ContentX hatası: {e}", "DEBUG")
    return None

def get_stream_url(episode_url):
    try:
        resp = session.get(episode_url, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        script = soup.find("script", id="__NEXT_DATA__")
        if not script: return None
        
        data = json.loads(script.string)
        secure_data = data.get("props", {}).get("pageProps", {}).get("secureData", "")
        decrypted = decrypt_dizilla_response(secure_data)
        
        if not decrypted: return None
        
        page_data = json.loads(decrypted)
        sources = page_data.get("RelatedResults", {}).get("getEpisodeSources", {}).get("result", [])
        
        if not sources: return None
        
        iframe_html = sources[0].get("source_content", "")
        iframe_src = BeautifulSoup(iframe_html, 'html.parser').find("iframe")
        
        if not iframe_src: return None
        
        src = iframe_src.get("src")
        if src.startswith("//"): src = "https:" + src
        
        if "contentx.me" in src or "hotlinger" in src:
            return extract_contentx(src, episode_url)
        
        return src
    except Exception as e:
        log(f"Stream hatası ({episode_url}): {e}", "DEBUG")
        return None

def scrape_latest():
    log("Siteye bağlanılıyor (WARP Aktif)...", "INFO")
    try:
        resp = session.get(f"{BASE_URL}/tum-bolumler", timeout=20)
        
        if resp.status_code == 403:
            log("Hala 403 hatası alınıyor. WARP IP'si de engellenmiş olabilir.", "CRITICAL")
            return []
            
        if resp.status_code != 200:
            log(f"Hata kodu: {resp.status_code}", "ERROR")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.select("div.grid a[href*='/dizi/']")
        
        log(f"{len(cards)} içerik bulundu.", "SUCCESS")
        
        items = []
        for card in cards[:20]: # İlk 20 bölüm
            try:
                href = card.get("href")
                full_url = f"{BASE_URL}{href}"
                title = card.find("h2").text.strip()
                ep_info = card.select_one("div.opacity-80").text.strip().replace(". Sezon ", "x").replace(". Bölüm", "")
                items.append({"title": f"{title} - {ep_info}", "url": full_url, "category": "Yeni Eklenenler"})
            except: continue
            
        return items
    except Exception as e:
        log(f"Bağlantı hatası: {e}", "CRITICAL")
        return []

def generate_m3u(items):
    if not items: return
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in items:
            log(f"İşleniyor: {item['title']}", "INFO")
            stream_url = get_stream_url(item["url"])
            
            if stream_url:
                final_url = stream_url
                if ".m3u8" in stream_url:
                    final_url = f"{stream_url}|User-Agent={session.headers['User-Agent']}"
                
                f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                f.write(f"{final_url}\n")
            else:
                log("Link bulunamadı.", "WARNING")
            time.sleep(0.5)

def main():
    items = scrape_latest()
    generate_m3u(items)

if __name__ == "__main__":
    main()
