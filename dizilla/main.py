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
# Sitemap Adresi (Google Translate üzerinden)
SITEMAP_URL = "https://www-dizibox-live.translate.goog/post-sitemap.xml?_x_tr_sl=auto&_x_tr_tl=tr&_x_tr_hl=tr&_x_tr_pto=wapp"
# Video sayfaları için proxy base
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
def get_translate_url(original_url):
    """Orijinal URL'i Google Translate URL'ine çevirir"""
    if "translate.goog" in original_url:
        return original_url
    
    # https://www.dizibox.live/dizi-adi... -> /dizi-adi...
    path = original_url.replace(REAL_BASE_URL, "")
    return f"{PROXY_BASE_URL}{path}{PROXY_PARAMS}"

# --- BAŞLIK AYIKLAYICI ---
def parse_title_from_url(url):
    """URL'den Dizi Adı ve Bölüm bilgisini çıkarır"""
    # Örnek: https://www.dizibox.live/the-price-of-confession-1-sezon-1-bolum-izle/
    try:
        # Domaini at, son slash'ı at
        slug = url.rstrip("/").split("/")[-1]
        
        # "-izle" kısmını at
        slug = slug.replace("-izle", "")
        
        # Sezon ve Bölüm bul
        # Regex: (.*)-(\d+)-sezon-(\d+)-bolum
        match = re.search(r"(.*)-(\d+)-sezon-(\d+)-bolum", slug)
        
        if match:
            name_slug = match.group(1)
            season = match.group(2)
            episode = match.group(3)
            
            # İsmi düzelt (tireleri boşluk yap, baş harfleri büyüt)
            name = name_slug.replace("-", " ").title()
            
            return f"{name} - {season}x{episode}"
        
        return slug.replace("-", " ").title()
    except:
        return "Bilinmeyen Dizi"

# --- VİDEO URL ÇIKARTMA ---
def extract_video_url(page, episode_url):
    try:
        safe_url = get_translate_url(episode_url)
        log(f"Video aranıyor: {safe_url}", "DEBUG")
        
        page.get(safe_url)
        
        if "Just a moment" in page.title:
            time.sleep(5)
        
        # 1. Iframe Bul (King Player)
        iframe_src = None
        
        # Sayfa kaynağında 'king.php' ara (En garantisi)
        html = page.html
        match = re.search(r'src="([^"]*king\.php[^"]*)"', html)
        
        if match:
            iframe_src = match.group(1)
        else:
            # Element olarak ara
            iframes = page.eles("tag:iframe")
            for ifr in iframes:
                src = ifr.attr("src")
                if src and "king.php" in src:
                    iframe_src = src
                    break
        
        if not iframe_src:
            log("King Player bulunamadı.", "WARNING")
            return None
            
        if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
        if "king.php" in iframe_src:
            iframe_src = iframe_src.replace("king.php?v=", "king.php?wmode=opaque&v=")
            
        # 2. Player'a git
        page.get(iframe_src)
        time.sleep(2)
        html = page.html
        
        # 3. Şifre Çözme (CryptoJS)
        # Senaryo A: Ana sayfada şifre
        match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
        if match_data:
            decrypted = decrypt_cryptojs(match_data.group(2), match_data.group(1))
            if decrypted:
                file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                if file_match: return file_match.group(1)

        # Senaryo B: İç içe iframe (div#Player)
        nested_match = re.search(r'<div id="Player">.*?<iframe.*?src="([^"]+)".*?>', html, re.DOTALL)
        if nested_match:
            nested_src = nested_match.group(1)
            if nested_src.startswith("//"): nested_src = "https:" + nested_src
            
            page.get(nested_src)
            time.sleep(2)
            html = page.html
            
            match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
            if match_data:
                decrypted = decrypt_cryptojs(match_data.group(2), match_data.group(1))
                if decrypted:
                    file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                    if file_match: return file_match.group(1)
            
            # M3U8 direkt var mı?
            m3u8_match = re.search(r'file:\s*"([^"]+\.m3u8[^"]*)"', html)
            if m3u8_match: return m3u8_match.group(1)

        return None

    except Exception as e:
        log(f"Hata: {e}", "ERROR")
        return None

def main():
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-translate')
    
    page = ChromiumPage(co)
    
    try:
        log("Sitemap Tarayıcı Başlatıldı.", "INFO")
        
        # 1. Sitemap'e Git
        log(f"Sitemap okunuyor: {SITEMAP_URL}", "INFO")
        page.get(SITEMAP_URL)
        
        time.sleep(5)
        if "Access denied" in page.title:
            log("HATA: Sitemap erişimi engellendi!", "CRITICAL")
            page.quit()
            return

        # 2. Linkleri Tablodan Çek
        # HTML yapısı: <td class="left"><a href="...">...</a></td>
        # DrissionPage ile bu yapıyı bulalım
        
        links = []
        # Tablo satırlarını al
        rows = page.eles("tag:tr")
        
        log(f"Sitemap'te {len(rows)} satır bulundu.", "INFO")
        
        for row in rows:
            try:
                # Sol hücredeki linki al
                a_tag = row.ele("css:td.left a")
                if not a_tag: continue
                
                href = a_tag.attr("href")
                if not href: continue
                
                # Sadece bölüm linklerini al (-izle ile bitenler)
                if "-izle" not in href: continue
                
                # Başlığı URL'den çıkar
                title = parse_title_from_url(href)
                
                links.append({
                    "title": title,
                    "url": href,
                    "category": "Son Eklenenler"
                })
                
                # İlk 20 bölümü alıp çıkalım (Sitemap çok büyük olabilir)
                if len(links) >= 20: break
                
            except: continue
            
        if not links:
            log("Sitemap'ten link çıkarılamadı. HTML yapısı farklı olabilir.", "CRITICAL")
            log(f"HTML DUMP: {page.html[:1000]}", "DEBUG")
        else:
            log(f"{len(links)} adet yeni bölüm bulundu.", "SUCCESS")
            
        # 3. Videoları Çek ve M3U Oluştur
        if links:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in links:
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
        else:
            log("Liste boş.", "WARNING")

    except Exception as e:
        log(f"Kritik Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
