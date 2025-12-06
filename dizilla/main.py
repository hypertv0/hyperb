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
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizilla.m3u"

# --- LOGLAMA ---
def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")
    sys.stdout.flush()

# --- CRYPTOJS ŞİFRE ÇÖZME (Python Portu) ---
# DiziBox player'ı CryptoJS kullanıyor. Bu fonksiyon OpenSSL KDF mantığını taklit eder.
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
        log(f"Şifre çözme hatası: {e}", "ERROR")
        return None

# --- VİDEO URL ÇIKARTMA ---
def extract_video_url(page, episode_url):
    try:
        page.get(episode_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title:
            time.sleep(5)
        
        # 1. Adım: Ana sayfadaki iframe'i bul (div#video-area iframe)
        # Kotlin kodunda: iframe.replace("king.php?v=", "king.php?wmode=opaque&v=")
        
        iframe_src = page.ele("css:div#video-area iframe").attr("src")
        if not iframe_src:
            log("Video iframe bulunamadı.", "WARNING")
            return None
            
        # URL düzeltmeleri
        if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
        if "king.php" in iframe_src:
            iframe_src = iframe_src.replace("king.php?v=", "king.php?wmode=opaque&v=")
            
        log(f"Player bulundu, gidiliyor: {iframe_src}", "DEBUG")
        
        # 2. Adım: Player iframe'ine git
        page.get(iframe_src)
        time.sleep(2)
        
        # 3. Adım: İçerdeki asıl player'ı veya şifreli veriyi bul
        # Senaryo A: Doğrudan CryptoJS verisi var
        html = page.html
        
        # bakalim.py mantığı: CryptoJS.AES.decrypt("DATA", "PASS")
        match_data = re.search(r'CryptoJS\.AES\.decrypt\("([^"]+)",\s*"([^"]+)"\)', html)
        
        if match_data:
            encrypted_data = match_data.group(1)
            passphrase = match_data.group(2)
            
            log("Şifreli veri bulundu, çözülüyor...", "INFO")
            decrypted = decrypt_cryptojs(passphrase, encrypted_data)
            
            if decrypted:
                # Çözülen verinin içinde "file": "..." arıyoruz
                file_match = re.search(r"file:\s*'([^']+)'", decrypted) or re.search(r'file:\s*"([^"]+)"', decrypted)
                if file_match:
                    return file_match.group(1)
        
        # Senaryo B: İç içe bir iframe daha var (div#Player iframe)
        nested_iframe = page.ele("css:div#Player iframe")
        if nested_iframe:
            nested_src = nested_iframe.attr("src")
            if nested_src:
                if nested_src.startswith("//"): nested_src = "https:" + nested_src
                log(f"İç iframe bulundu: {nested_src}", "DEBUG")
                
                # Sheila/Vidmoly kontrolü (Kotlin kodundan)
                if "sheila" in nested_src or "vidmoly" in nested_src:
                    # Vidmoly extractor mantığı gerekebilir ama şimdilik direkt linki döndürelim
                    # veya içine girip m3u8 arayalım.
                    page.get(nested_src)
                    time.sleep(2)
                    m3u8_match = re.search(r'file:\s*"([^"]+\.m3u8[^"]*)"', page.html)
                    if m3u8_match:
                        return m3u8_match.group(1)
                    
        return None

    except Exception as e:
        log(f"Video işleme hatası: {e}", "ERROR")
        return None

def main():
    # DrissionPage Ayarları
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    
    # DiziBox için gerekli çerezler (Kotlin kodundan alındı)
    # Bu çerezler "Adblock kapat" uyarısını ve bazı engelleri aşar.
    co.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    page = ChromiumPage(co)
    
    # Çerezleri ayarla
    page.set.cookies([
        {'name': 'LockUser', 'value': 'true', 'domain': '.dizibox.live'},
        {'name': 'isTrustedUser', 'value': 'true', 'domain': '.dizibox.live'},
        {'name': 'dbxu', 'value': '1744054959089', 'domain': '.dizibox.live'}
    ])

    try:
        log("DiziBox Tarayıcı Başlatıldı.", "INFO")
        
        # 1. Son Bölümler Sayfasına Git
        url = f"{BASE_URL}/tum-bolumler/"
        page.get(url)
        
        # Cloudflare Kontrolü
        if "Just a moment" in page.title:
            log("Cloudflare bekleniyor...", "WARNING")
            time.sleep(6)
            
        if "Just a moment" in page.title:
            log("Cloudflare aşılamadı.", "CRITICAL")
            page.quit()
            return

        log("Ana sayfaya erişildi.", "SUCCESS")
        
        # 2. Bölümleri Topla
        # Seçici: article.detailed-article (Kotlin kodundan)
        articles = page.eles("css:article.detailed-article")
        
        if not articles:
            # Alternatif seçici
            articles = page.eles("css:article.article-episode-card")
            
        log(f"{len(articles)} adet bölüm bulundu.", "INFO")
        
        items = []
        for article in articles[:15]: # İlk 15 bölüm
            try:
                # Başlık ve Link
                h3_a = article.ele("css:h3 a")
                if not h3_a: continue
                
                title = h3_a.text.strip()
                href = h3_a.attr("href")
                
                # Resim (Opsiyonel)
                img = article.ele("tag:img")
                poster = img.attr("data-src") or img.attr("src") if img else ""
                
                # Başlık temizleme (Dizi Adı - Sezon x Bölüm)
                # Genelde title zaten düzgün gelir ama kontrol edelim.
                
                full_url = href
                if not full_url.startswith("http"):
                    full_url = BASE_URL + full_url
                
                items.append({
                    "title": title,
                    "url": full_url,
                    "category": "Son Eklenenler",
                    "poster": poster
                })
            except: continue
            
        # 3. Linkleri Çöz ve M3U Oluştur
        if items:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in items:
                    log(f"İşleniyor: {item['title']}", "INFO")
                    stream_url = extract_video_url(page, item["url"])
                    
                    if stream_url:
                        # User-Agent ekle
                        final_url = stream_url
                        if ".m3u8" in stream_url:
                            final_url = f"{stream_url}|User-Agent={page.user_agent}"
                        
                        f.write(f'#EXTINF:-1 group-title="{item["category"]}" tvg-logo="{item["poster"]}", {item["title"]}\n')
                        f.write(f"{final_url}\n")
                        log("Eklendi.", "SUCCESS")
                    else:
                        log("Stream bulunamadı.", "WARNING")
                    
                    time.sleep(1)
        else:
            log("Liste boş, içerik çekilemedi.", "WARNING")

    except Exception as e:
        log(f"Genel Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
