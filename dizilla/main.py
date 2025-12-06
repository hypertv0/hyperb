import json
import base64
import time
import re
import sys
import hashlib
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
REAL_BASE_URL = "https://www.dizibox.live"
# Google Translate Proxy URL'si
PROXY_BASE_URL = "https://www-dizibox-live.translate.goog"
PROXY_PARAMS = "?_x_tr_sl=auto&_x_tr_tl=tr&_x_tr_hl=tr&_x_tr_pto=wapp"

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
        log(f"Decryption Error: {e}", "ERROR")
        return None

# --- URL DÖNÜŞTÜRÜCÜ ---
def get_translate_url(original_path):
    """Normal yolu Google Translate URL'ine çevirir"""
    # /dizi-adi-izle/ -> https://www-dizibox-live.translate.goog/dizi-adi-izle/?...
    if original_path.startswith("http"):
        if "translate.goog" in original_path:
            return original_path
        # Tam URL ise path'i al
        original_path = original_path.replace(REAL_BASE_URL, "")
    
    return f"{PROXY_BASE_URL}{original_path}{PROXY_PARAMS}"

# --- VİDEO URL ÇIKARTMA ---
def extract_video_url(page, episode_url):
    try:
        # Google Translate üzerinden git
        safe_url = get_translate_url(episode_url)
        log(f"Google üzerinden gidiliyor: {safe_url}", "DEBUG")
        
        page.get(safe_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title:
            time.sleep(5)
        
        # 1. Adım: Iframe Bul (King Player)
        # Google Translate iframe'leri değiştirebilir, geniş arama yapalım
        # Orijinal: src=".../king.php?v=..."
        
        iframe_src = None
        
        # Tüm iframe'leri tara
        iframes = page.eles("tag:iframe")
        for ifr in iframes:
            src = ifr.attr("src")
            if src and "king.php" in src:
                iframe_src = src
                break
        
        if not iframe_src:
            # Belki HTML text içinde vardır (Google bazen iframe'i render etmez)
            html = page.html
            match = re.search(r'src="([^"]*king\.php[^"]*)"', html)
            if match:
                iframe_src = match.group(1)

        if not iframe_src:
            log("Video iframe (King) bulunamadı.", "WARNING")
            return None
            
        # URL düzeltmeleri (Google Translate URL'ini temizle veya olduğu gibi kullan)
        # Eğer iframe src'si translate.goog ise, onu kullanabiliriz.
        # Değilse, manuel çevirelim.
        
        # King.php linkini alıp temizleyelim
        # Örnek: https://www-dizibox-live.translate.goog/player/king.php?...
        
        if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
        
        # wmode ekle
        if "king.php" in iframe_src:
            iframe_src = iframe_src.replace("king.php?v=", "king.php?wmode=opaque&v=")
            
        log(f"Player bulundu: {iframe_src}", "DEBUG")
        
        # 2. Adım: Player'a git
        page.get(iframe_src)
        time.sleep(2)
        
        # 3. Adım: İçerdeki asıl player'ı bul (div#Player iframe)
        # Google Translate içinde element seçimi zor olabilir, HTML regex kullanalım
        html = page.html
        
        # Senaryo A: Doğrudan CryptoJS verisi var mı?
        match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
        
        if match_data:
            encrypted_data = match_data.group(1)
            passphrase = match_data.group(2)
            
            log("Şifreli veri bulundu (Ana Player), çözülüyor...", "INFO")
            decrypted = decrypt_cryptojs(passphrase, encrypted_data)
            
            if decrypted:
                file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                if file_match:
                    return file_match.group(1)

        # Senaryo B: İç içe iframe (div#Player)
        # Regex ile src bulalım
        nested_match = re.search(r'<div id="Player">.*?<iframe.*?src="([^"]+)".*?>', html, re.DOTALL)
        if nested_match:
            nested_src = nested_match.group(1)
            if nested_src.startswith("//"): nested_src = "https:" + nested_src
            
            # Google Translate URL'i olabilir, sorun değil
            log(f"Kaynak player bulundu: {nested_src}", "DEBUG")
            
            page.get(nested_src)
            time.sleep(2)
            html = page.html
            
            # Tekrar CryptoJS ara
            match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
            if match_data:
                encrypted_data = match_data.group(1)
                passphrase = match_data.group(2)
                
                log("Şifreli veri bulundu (İç Player), çözülüyor...", "INFO")
                decrypted = decrypt_cryptojs(passphrase, encrypted_data)
                
                if decrypted:
                    file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                    if file_match:
                        return file_match.group(1)
            
            # M3U8 ara
            m3u8_match = re.search(r'file:\s*"([^"]+\.m3u8[^"]*)"', html)
            if m3u8_match:
                return m3u8_match.group(1)

        return None

    except Exception as e:
        log(f"Video işleme hatası: {e}", "ERROR")
        return None

def main():
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-translate') # Google Translate barını kapat
    
    page = ChromiumPage(co)
    
    try:
        log("DiziBox (Google Proxy) Başlatıldı.", "INFO")
        
        # 1. Son Bölümler Sayfasına Git (Google Translate üzerinden)
        start_url = f"{PROXY_BASE_URL}/tum-bolumler/{PROXY_PARAMS}"
        log(f"Bağlanılıyor: {start_url}", "INFO")
        
        page.get(start_url)
        
        # Cloudflare Kontrolü
        time.sleep(5)
        if "Access denied" in page.title:
            log("HATA: Google Translate üzerinden bile engellendi!", "CRITICAL")
            page.quit()
            return

        log("Ana sayfaya erişildi. Linkler taranıyor...", "SUCCESS")
        
        # 2. Bölümleri Topla
        # Google Translate linkleri değiştirir.
        # Linkler: https://www-dizibox-live.translate.goog/dizi-adi-izle/?...
        
        all_links = page.eles("tag:a")
        log(f"Sayfada {len(all_links)} link bulundu.", "INFO")
        
        items = []
        for link in all_links:
            try:
                href = link.attr("href")
                if not href: continue
                
                # Filtre: DiziBox bölüm linki mi?
                # Orijinalde "-izle" ve "-sezon-" içerir.
                # Translate URL'inde de bu path korunur.
                
                if "-izle" in href and "-sezon-" in href:
                    
                    # Başlık Çıkarma
                    # Linkin içindeki metne veya resme bakalım
                    link_html = link.html
                    soup = BeautifulSoup(link_html, 'html.parser')
                    
                    # H3 veya IMG alt
                    h3 = soup.find("h3")
                    if h3:
                        title = h3.text.strip()
                    else:
                        img = soup.find("img")
                        if img and img.get("alt"):
                            title = img.get("alt")
                        else:
                            title = soup.get_text().strip()
                    
                    if not title: continue
                    
                    # Poster
                    img_tag = soup.find("img")
                    poster = ""
                    if img_tag:
                        poster = img_tag.get("data-src") or img_tag.get("src") or ""

                    # Tekrarı önle
                    # href zaten Google Translate URL'i, bunu direkt kullanabiliriz
                    if not any(d['title'] == title for d in items):
                        items.append({
                            "title": title,
                            "url": href, # Bu URL zaten translate.goog domainli
                            "category": "Son Eklenenler",
                            "poster": poster
                        })
                        
                if len(items) >= 20: break
            except: continue
            
        if len(items) == 0:
            log("HİÇ BÖLÜM BULUNAMADI! HTML yapısı debug ediliyor...", "CRITICAL")
            # log(f"HTML DUMP: {page.html[:1000]}", "DEBUG")
        else:
            log(f"{len(items)} adet bölüm bulundu. Linkler çözülüyor...", "INFO")
            
        # 3. Linkleri Çöz ve M3U Oluştur
        if items:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in items:
                    log(f"İşleniyor: {item['title']}", "INFO")
                    # item['url'] zaten translate linki, direkt gönderiyoruz
                    stream_url = extract_video_url(page, item["url"])
                    
                    if stream_url:
                        ua = page.user_agent
                        final_url = stream_url
                        if ".m3u8" in stream_url:
                            final_url = f"{stream_url}|User-Agent={ua}"
                        
                        logo_attr = f'tvg-logo="{item["poster"]}"' if item["poster"] else ""
                        
                        f.write(f'#EXTINF:-1 group-title="{item["category"]}" {logo_attr}, {item["title"]}\n')
                        f.write(f"{final_url}\n")
                        log("Eklendi.", "SUCCESS")
                    else:
                        log("Stream bulunamadı.", "WARNING")
                    
                    time.sleep(1)
        else:
            log("Liste boş.", "WARNING")

    except Exception as e:
        log(f"Kritik Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
