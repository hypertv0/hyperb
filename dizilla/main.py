import os
import json
import base64
import re
import time
import cloudscraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://dizilla40.com"
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
AES_IV = bytes([0] * 16)
OUTPUT_FILE = "dizilla.m3u"

# Cloudscraper ayarları (Masaüstü Chrome taklidi)
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def decrypt_dizilla_response(encrypted_data):
    try:
        if not encrypted_data: return None
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        return None

def get_headers(referer=BASE_URL):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": BASE_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }

def extract_contentx(url, referer):
    try:
        resp = scraper.get(url, headers=get_headers(referer), timeout=10)
        match = re.search(r"window\.openPlayer\('([^']+)'", resp.text)
        if not match: return None
        
        extract_id = match.group(1)
        source_url = f"https://contentx.me/source2.php?v={extract_id}"
        vid_resp = scraper.get(source_url, headers=get_headers(url), timeout=10)
        
        vid_match = re.search(r'file":"([^"]+)"', vid_resp.text)
        if vid_match:
            return vid_match.group(1).replace("\\", "")
    except:
        pass
    return None

def get_stream_url(episode_url):
    try:
        resp = scraper.get(episode_url, headers=get_headers(), timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        script = soup.find("script", id="__NEXT_DATA__")
        if not script: return None
            
        data = json.loads(script.string)
        secure_data_enc = data.get("props", {}).get("pageProps", {}).get("secureData", "")
        
        decrypted_json = decrypt_dizilla_response(secure_data_enc)
        if not decrypted_json: return None
            
        page_data = json.loads(decrypted_json)
        sources = page_data.get("RelatedResults", {}).get("getEpisodeSources", {}).get("result", [])
        
        if not sources: return None
        
        source_content = sources[0].get("source_content", "")
        iframe_src = BeautifulSoup(source_content, 'html.parser').find("iframe")
        
        if not iframe_src: return None
        src = iframe_src.get("src")
        
        if src.startswith("//"): src = "https:" + src
            
        if "contentx.me" in src or "hotlinger" in src:
            return extract_contentx(src, episode_url)
        
        return src
    except:
        return None

def scrape_latest_episodes():
    print("-> Siteye bağlanılıyor...")
    url = f"{BASE_URL}/tum-bolumler"
    
    try:
        resp = scraper.get(url, headers=get_headers(), timeout=15)
        
        if resp.status_code != 200:
            print(f"HATA: Site {resp.status_code} kodu döndürdü.")
            return []
            
        if "Just a moment" in resp.text:
            print("HATA: Cloudflare engeline takıldı.")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Seçiciyi genişlettim, sadece col-span-3 değil, genel link yapısına bakıyoruz
        cards = soup.select("div.grid a[href*='/dizi/']")
        
        if not cards:
            print("UYARI: HTML geldi ama bölüm kartları bulunamadı. Site tasarımı değişmiş olabilir.")
            # Debug için HTML'in bir kısmını yazdıralım
            # print(resp.text[:500]) 
            return []

        print(f"-> {len(cards)} adet potansiyel içerik bulundu.")
        
        items = []
        for card in cards:
            try:
                href = card.get("href")
                if not href: continue
                
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                
                # Başlık ve Bölüm bilgisini daha güvenli çekelim
                title_div = card.find("h2")
                ep_div = card.select_one("div.opacity-80")
                
                if not title_div or not ep_div:
                    continue

                title = title_div.text.strip()
                ep_info = ep_div.text.strip().replace(". Sezon ", "x").replace(". Bölüm", "")
                
                full_title = f"{title} - {ep_info}"
                
                items.append({
                    "title": full_title,
                    "url": full_url,
                    "category": "Yeni Eklenenler"
                })
            except:
                continue
        
        return items

    except Exception as e:
        print(f"HATA: Bağlantı sorunu: {e}")
        return []

def generate_m3u(playlist_items):
    if not playlist_items:
        print("M3U oluşturulacak içerik yok.")
        return

    print(f"-> M3U dosyası yazılıyor... ({len(playlist_items)} içerik)")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for i, item in enumerate(playlist_items):
            print(f"   [{i+1}/{len(playlist_items)}] Link çözülüyor: {item['title']}")
            stream_url = get_stream_url(item["url"])
            
            if stream_url:
                final_url = stream_url
                # Eğer m3u8 ise User-Agent ekle
                if ".m3u8" in stream_url:
                    final_url = f"{stream_url}|User-Agent={get_headers()['User-Agent']}"
                
                f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                f.write(f"{final_url}\n")
            else:
                print(f"   X Link bulunamadı.")
            
            time.sleep(1) # Sunucuyu boğmamak için bekleme

def main():
    latest = scrape_latest_episodes()
    generate_m3u(latest)
    print("İşlem tamamlandı.")

if __name__ == "__main__":
    main()
