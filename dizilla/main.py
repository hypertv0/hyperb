import json
import base64
import time
import re
from datetime import datetime
from seleniumbase import SB
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

def extract_contentx(sb, url):
    try:
        sb.open(url)
        time.sleep(2) # Yüklenmesini bekle
        source = sb.get_page_source()
        
        match = re.search(r"window\.openPlayer\('([^']+)'", source)
        if match:
            extract_id = match.group(1)
            source_url = f"https://contentx.me/source2.php?v={extract_id}"
            sb.open(source_url)
            time.sleep(1)
            vid_source = sb.get_page_source()
            vid_match = re.search(r'file":"([^"]+)"', vid_source)
            if vid_match:
                return vid_match.group(1).replace("\\", "")
    except Exception as e:
        log(f"ContentX hatası: {e}", "DEBUG")
    return None

def process_episode(sb, episode_url):
    try:
        sb.open(episode_url)
        # Cloudflare kontrolü varsa bekle
        if "Just a moment" in sb.get_page_title():
            log("Cloudflare kontrolü bekleniyor...", "WARNING")
            time.sleep(6)
        
        soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
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
            return extract_contentx(sb, src)
        
        return src
    except Exception as e:
        log(f"Bölüm işleme hatası: {e}", "ERROR")
        return None

def main():
    # UC Mode: Undetected Chrome
    # headless=False: Gerçek tarayıcı gibi davranması için (Xvfb ile sanal ekranda çalışacak)
    with SB(uc=True, test=True, headless=False, locale_code="tr") as sb:
        log("Tarayıcı başlatıldı (UC Mode).", "INFO")
        
        # 1. Ana Sayfaya Git
        url = f"{BASE_URL}/tum-bolumler"
        sb.open(url)
        
        # Cloudflare Turnstile/Challenge kontrolü
        log("Sayfa yükleniyor, Cloudflare kontrolü yapılıyor...", "INFO")
        try:
            # Eğer CF çıkarsa SeleniumBase otomatik tıklamayı dener
            sb.uc_gui_click_captcha() 
        except:
            pass
        
        time.sleep(5) # Sayfanın tam oturmasını bekle
        
        if "Just a moment" in sb.get_page_title() or "Access denied" in sb.get_page_title():
            log("HATA: Cloudflare aşılamadı.", "CRITICAL")
            # Sayfa kaynağını debug için yazdır
            # print(sb.get_page_source()[:500])
            return

        log("Ana sayfaya erişildi!", "SUCCESS")
        
        # 2. Linkleri Topla
        soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
        cards = soup.select("div.grid a[href*='/dizi/']")
        
        items = []
        for card in cards[:15]: # İlk 15 bölüm
            try:
                href = card.get("href")
                full_url = f"{BASE_URL}{href}"
                title = card.find("h2").text.strip()
                ep_info = card.select_one("div.opacity-80").text.strip().replace(". Sezon ", "x").replace(". Bölüm", "")
                items.append({"title": f"{title} - {ep_info}", "url": full_url, "category": "Yeni"})
            except: continue
            
        log(f"{len(items)} içerik bulundu. Linkler çözülüyor...", "INFO")
        
        # 3. M3U Oluştur
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            for item in items:
                log(f"İşleniyor: {item['title']}", "INFO")
                stream_url = process_episode(sb, item["url"])
                
                if stream_url:
                    # User-Agent'ı tarayıcıdan al
                    ua = sb.get_user_agent()
                    final_url = f"{stream_url}|User-Agent={ua}" if ".m3u8" in stream_url else stream_url
                    
                    f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                    f.write(f"{final_url}\n")
                    log("Eklendi.", "SUCCESS")
                else:
                    log("Link bulunamadı.", "WARNING")
                
                time.sleep(1)

if __name__ == "__main__":
    main()
