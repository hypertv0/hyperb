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

# --- LOGLAMA FONKSİYONU ---
def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")

# --- ŞİFRE ÇÖZME ---
def decrypt_dizilla_response(encrypted_data):
    try:
        if not encrypted_data:
            log("Şifreli veri boş geldi.", "WARNING")
            return None
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        log(f"Şifre çözme hatası: {e}", "ERROR")
        return None

# --- İSTEK OTURUMU (TLS TAKLİDİ) ---
# Chrome 120 tarayıcısını taklit eden bir oturum açıyoruz
session = requests.Session(impersonate="chrome120")
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL,
    "Origin": BASE_URL,
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1"
})

def extract_contentx(url, referer):
    log(f"ContentX/Hotlinger işleniyor: {url}", "DEBUG")
    try:
        resp = session.get(url, headers={"Referer": referer}, timeout=10)
        if resp.status_code != 200:
            log(f"ContentX erişim hatası: {resp.status_code}", "ERROR")
            return None

        match = re.search(r"window\.openPlayer\('([^']+)'", resp.text)
        if not match:
            log("ContentX içinde openPlayer ID bulunamadı.", "WARNING")
            return None
        
        extract_id = match.group(1)
        source_url = f"https://contentx.me/source2.php?v={extract_id}"
        
        vid_resp = session.get(source_url, headers={"Referer": url}, timeout=10)
        vid_match = re.search(r'file":"([^"]+)"', vid_resp.text)
        
        if vid_match:
            m3u = vid_match.group(1).replace("\\", "")
            log(f"M3U8 Bulundu: {m3u[:50]}...", "SUCCESS")
            return m3u
        else:
            log("ContentX source2.php içinde dosya bulunamadı.", "WARNING")
            
    except Exception as e:
        log(f"ContentX hatası: {e}", "ERROR")
    return None

def get_stream_url(episode_url):
    log(f"Bölüm verisi çekiliyor: {episode_url}", "INFO")
    try:
        resp = session.get(episode_url, timeout=15)
        if resp.status_code != 200:
            log(f"Bölüm sayfasına girilemedi. Kod: {resp.status_code}", "ERROR")
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        script = soup.find("script", id="__NEXT_DATA__")
        
        if not script:
            log("__NEXT_DATA__ script etiketi bulunamadı.", "ERROR")
            return None
            
        data = json.loads(script.string)
        secure_data_enc = data.get("props", {}).get("pageProps", {}).get("secureData", "")
        
        if not secure_data_enc:
            log("secureData bulunamadı.", "ERROR")
            return None
            
        decrypted_json = decrypt_dizilla_response(secure_data_enc)
        if not decrypted_json: return None
            
        page_data = json.loads(decrypted_json)
        sources = page_data.get("RelatedResults", {}).get("getEpisodeSources", {}).get("result", [])
        
        if not sources:
            log("Kaynak listesi boş.", "WARNING")
            return None
        
        # İlk kaynağı al
        source_content = sources[0].get("source_content", "")
        iframe_soup = BeautifulSoup(source_content, 'html.parser')
        iframe = iframe_soup.find("iframe")
        
        if not iframe:
            log("Iframe bulunamadı.", "WARNING")
            return None
            
        src = iframe.get("src")
        if src.startswith("//"): src = "https:" + src
        
        log(f"Iframe Kaynağı: {src}", "DEBUG")
            
        if "contentx.me" in src or "hotlinger" in src:
            return extract_contentx(src, episode_url)
        
        return src

    except Exception as e:
        log(f"Stream bulma genel hatası: {e}", "ERROR")
        return None

def scrape_latest_episodes():
    url = f"{BASE_URL}/tum-bolumler"
    log(f"Ana sayfaya bağlanılıyor: {url}", "INFO")
    
    try:
        resp = session.get(url, timeout=20)
        
        log(f"Sunucu Yanıt Kodu: {resp.status_code}", "INFO")
        
        if resp.status_code == 403:
            log("HATA: 403 Forbidden. Cloudflare engeli devam ediyor.", "CRITICAL")
            log("Dönen Headerlar: " + str(resp.headers), "DEBUG")
            log("Dönen İçerik (İlk 500 karakter): " + resp.text[:500], "DEBUG")
            return []
            
        if resp.status_code != 200:
            log(f"Beklenmeyen durum kodu: {resp.status_code}", "ERROR")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # HTML yapısını kontrol et
        cards = soup.select("div.grid a[href*='/dizi/']")
        if not cards:
            log("HTML geldi fakat 'div.grid a' seçicisi ile içerik bulunamadı.", "WARNING")
            log("Sayfa başlığı: " + (soup.title.string if soup.title else "Yok"), "DEBUG")
            # Alternatif seçici dene
            cards = soup.select("a[href*='/dizi/']")
            log(f"Alternatif seçici ile {len(cards)} link bulundu.", "INFO")

        log(f"Toplam {len(cards)} adet potansiyel kart bulundu.", "INFO")
        
        items = []
        for i, card in enumerate(cards):
            # Çok fazla tarayıp ban yememek için ilk 10 tanesini test edelim (İstersen sayıyı artır)
            if i >= 20: break 
            
            try:
                href = card.get("href")
                if not href: continue
                
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                
                # Başlık bulma denemeleri
                title_tag = card.find("h2")
                if not title_tag:
                    # H2 yoksa belki sadece text vardır veya yapı farklıdır
                    continue
                
                title = title_tag.text.strip()
                
                # Bölüm bilgisi
                ep_tag = card.select_one("div.opacity-80")
                ep_info = ep_tag.text.strip().replace(". Sezon ", "x").replace(". Bölüm", "") if ep_tag else "?x?"
                
                full_title = f"{title} - {ep_info}"
                
                log(f"Bulundu: {full_title}", "DEBUG")
                
                items.append({
                    "title": full_title,
                    "url": full_url,
                    "category": "Yeni Eklenenler"
                })
            except Exception as e:
                log(f"Kart işleme hatası: {e}", "ERROR")
                continue
        
        return items

    except Exception as e:
        log(f"Ana sayfa tarama hatası: {e}", "CRITICAL")
        return []

def generate_m3u(playlist_items):
    if not playlist_items:
        log("Listeye eklenecek içerik yok, M3U oluşturulmadı.", "WARNING")
        return

    log(f"M3U dosyası oluşturuluyor... ({len(playlist_items)} içerik)", "INFO")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for item in playlist_items:
            stream_url = get_stream_url(item["url"])
            
            if stream_url:
                final_url = stream_url
                if ".m3u8" in stream_url:
                    # User-Agent'ı M3U içine gömüyoruz (Tivimate vb. için)
                    ua = session.headers["User-Agent"]
                    final_url = f"{stream_url}|User-Agent={ua}"
                
                f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                f.write(f"{final_url}\n")
                log(f"Eklendi: {item['title']}", "SUCCESS")
            else:
                log(f"Link çekilemedi: {item['title']}", "ERROR")
            
            time.sleep(1) # Nezaket beklemesi

def main():
    log("Script başlatıldı.", "INFO")
    latest = scrape_latest_episodes()
    generate_m3u(latest)
    log("Script tamamlandı.", "INFO")

if __name__ == "__main__":
    main()
