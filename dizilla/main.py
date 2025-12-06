import os
import json
import base64
import re
import time
import random
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

# --- LOGLAMA ---
def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")

# --- PROXY YÖNETİMİ ---
def get_proxies():
    log("Ücretsiz proxy listesi çekiliyor...", "INFO")
    try:
        # Proxyscrape API'den http proxyleri al
        resp = requests.get("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all")
        if resp.status_code == 200:
            proxies = resp.text.strip().split('\n')
            log(f"{len(proxies)} adet proxy bulundu.", "INFO")
            return [p.strip() for p in proxies if p.strip()]
    except Exception as e:
        log(f"Proxy listesi alınamadı: {e}", "ERROR")
    return []

def create_session(proxy=None):
    session = requests.Session(impersonate="chrome110")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Referer": BASE_URL,
        "Origin": BASE_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    if proxy:
        session.proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    return session

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

# --- İÇERİK ÇEKME ---
def extract_contentx(url, session):
    try:
        resp = session.get(url, headers={"Referer": BASE_URL}, timeout=10)
        match = re.search(r"window\.openPlayer\('([^']+)'", resp.text)
        if match:
            extract_id = match.group(1)
            source_url = f"https://contentx.me/source2.php?v={extract_id}"
            vid_resp = session.get(source_url, headers={"Referer": url}, timeout=10)
            vid_match = re.search(r'file":"([^"]+)"', vid_resp.text)
            if vid_match:
                return vid_match.group(1).replace("\\", "")
    except:
        pass
    return None

def get_stream_url(episode_url, session):
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
            return extract_contentx(src, session)
        
        return src
    except:
        return None

def scrape_with_proxy_rotation():
    proxies = get_proxies()
    # Proxy listesini karıştır
    random.shuffle(proxies)
    
    # Proxy olmadan önce bir dene (Belki şanslıyızdır)
    proxies.insert(0, None) 
    
    for proxy in proxies[:20]: # En fazla 20 proxy dene
        proxy_str = proxy if proxy else "Doğrudan Bağlantı"
        log(f"Deniyor: {proxy_str}", "INFO")
        
        try:
            session = create_session(proxy)
            url = f"{BASE_URL}/tum-bolumler"
            resp = session.get(url, timeout=10)
            
            if resp.status_code == 200 and "Just a moment" not in resp.text:
                log("BAŞARILI! Siteye erişildi.", "SUCCESS")
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                cards = soup.select("div.grid a[href*='/dizi/']")
                
                items = []
                for card in cards[:15]: # İlk 15 bölümü al
                    try:
                        href = card.get("href")
                        full_url = f"{BASE_URL}{href}"
                        title = card.find("h2").text.strip()
                        ep_info = card.select_one("div.opacity-80").text.strip().replace(". Sezon ", "x").replace(". Bölüm", "")
                        items.append({"title": f"{title} - {ep_info}", "url": full_url, "category": "Yeni"})
                    except: continue
                
                return items, session
            else:
                log(f"Başarısız. Kod: {resp.status_code}", "WARNING")
                
        except Exception as e:
            log(f"Proxy hatası: {str(e)[:50]}...", "WARNING")
            
    return [], None

def generate_m3u(items, session):
    if not items: return
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in items:
            log(f"Link çözülüyor: {item['title']}", "INFO")
            stream_url = get_stream_url(item["url"], session)
            if stream_url:
                final_url = f"{stream_url}|User-Agent={session.headers['User-Agent']}" if ".m3u8" in stream_url else stream_url
                f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                f.write(f"{final_url}\n")
                log("Eklendi.", "SUCCESS")
            else:
                log("Stream bulunamadı.", "ERROR")

def main():
    items, session = scrape_with_proxy_rotation()
    if items:
        generate_m3u(items, session)
    else:
        log("Hiçbir proxy ile siteye erişilemedi.", "CRITICAL")

if __name__ == "__main__":
    main()
