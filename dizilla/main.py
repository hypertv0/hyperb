import json
import base64
import time
import re
import os
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
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

def decrypt_dizilla_response(encrypted_data):
    try:
        if not encrypted_data: return None
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except:
        return None

def extract_contentx(page, url):
    try:
        tab = page.new_tab(url)
        time.sleep(3)
        
        # HTML kaynağını al
        html = tab.html
        match = re.search(r"window\.openPlayer\('([^']+)'", html)
        
        if match:
            extract_id = match.group(1)
            source_url = f"https://contentx.me/source2.php?v={extract_id}"
            tab.get(source_url)
            time.sleep(1)
            
            vid_html = tab.html
            vid_match = re.search(r'file":"([^"]+)"', vid_html)
            
            tab.close() # Sekmeyi kapat
            if vid_match:
                return vid_match.group(1).replace("\\", "")
        else:
            tab.close()
    except Exception as e:
        log(f"ContentX hatası: {e}", "DEBUG")
        try: tab.close() 
        except: pass
    return None

def process_episode(page, episode_url):
    try:
        page.get(episode_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title or "Access denied" in page.title:
            log("Cloudflare engeli algılandı, bekleniyor...", "WARNING")
            time.sleep(5)
            # Turnstile iframe'i varsa tıkla (DrissionPage bazen otomatik geçer)
            if page.ele("@class=cf-turnstile"):
                log("Turnstile bulundu, tıklanıyor...", "INFO")
                page.ele("@class=cf-turnstile").click()
                time.sleep(5)

        # __NEXT_DATA__ scriptini çek
        script_text = page.ele("#__NEXT_DATA__").text
        if not script_text: return None
        
        data = json.loads(script_text)
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
            return extract_contentx(page, src)
        
        return src
    except Exception as e:
        log(f"Bölüm işleme hatası: {e}", "ERROR")
        return None

def main():
    # DrissionPage Ayarları
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    # Headless modunu KAPATIYORUZ (Xvfb içinde çalışacak)
    # Bu sayede Cloudflare gerçek bir ekran görüyor.
    
    # Tarayıcıyı başlat
    page = ChromiumPage(co)
    
    try:
        log("DrissionPage Tarayıcı Başlatıldı.", "INFO")
        
        # 1. Ana Sayfaya Git
        url = f"{BASE_URL}/tum-bolumler"
        page.get(url)
        
        # Cloudflare Bekleme Mantığı
        for i in range(10):
            title = page.title
            if "Just a moment" in title or "Access denied" in title:
                log(f"Cloudflare kontrolü sürüyor... ({i+1}/10)", "WARNING")
                time.sleep(3)
            else:
                break
        
        if "Just a moment" in page.title:
            log("HATA: Cloudflare aşılamadı.", "CRITICAL")
            # Sayfa kaynağını kaydet (Debug için)
            # with open("debug.html", "w") as f: f.write(page.html)
            page.quit()
            return

        log("Ana sayfaya erişildi!", "SUCCESS")
        
        # 2. Linkleri Topla
        # DrissionPage element seçicileri çok güçlüdür
        cards = page.eles("css:div.grid a[href*='/dizi/']")
        
        items = []
        for card in cards[:15]: # İlk 15 bölüm
            try:
                href = card.attr("href")
                full_url = f"{BASE_URL}{href}"
                
                # HTML yapısını parse et
                card_html = card.html
                soup = BeautifulSoup(card_html, 'html.parser')
                
                title = soup.find("h2").text.strip()
                ep_info = soup.select_one("div.opacity-80").text.strip().replace(". Sezon ", "x").replace(". Bölüm", "")
                
                items.append({"title": f"{title} - {ep_info}", "url": full_url, "category": "Yeni"})
            except: continue
            
        log(f"{len(items)} içerik bulundu. Linkler çözülüyor...", "INFO")
        
        # 3. M3U Oluştur
        if items:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in items:
                    log(f"İşleniyor: {item['title']}", "INFO")
                    stream_url = process_episode(page, item["url"])
                    
                    if stream_url:
                        ua = page.user_agent
                        final_url = f"{stream_url}|User-Agent={ua}" if ".m3u8" in stream_url else stream_url
                        
                        f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                        f.write(f"{final_url}\n")
                        log("Eklendi.", "SUCCESS")
                    else:
                        log("Link bulunamadı.", "WARNING")
                    
                    time.sleep(1)
        else:
            log("Hiç içerik bulunamadı.", "WARNING")

    except Exception as e:
        log(f"Genel Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
