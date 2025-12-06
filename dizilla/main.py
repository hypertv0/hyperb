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

def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")
    sys.stdout.flush()

# --- CRYPTOJS ŞİFRE ÇÖZME (OpenSSL KDF Mantığı) ---
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

# --- VİDEO URL ÇIKARTMA ---
def extract_video_url(page, episode_url):
    try:
        page.get(episode_url)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title:
            time.sleep(5)
        
        # 1. Adım: Ana sayfadaki iframe'i bul (div#video-area iframe)
        # Kotlin kodunda: iframe.replace("king.php?v=", "king.php?wmode=opaque&v=")
        
        # DrissionPage ile iframe elementini bul
        iframe_ele = page.ele("css:div#video-area iframe")
        if not iframe_ele:
            # Alternatif: Belki iframe direkt sayfadadır
            iframe_ele = page.ele("css:iframe[src*='king.php']")
            
        if not iframe_ele:
            log("Video iframe (King) bulunamadı.", "WARNING")
            return None
            
        iframe_src = iframe_ele.attr("src")
        
        # URL düzeltmeleri
        if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
        if "king.php" in iframe_src:
            iframe_src = iframe_src.replace("king.php?v=", "king.php?wmode=opaque&v=")
            
        log(f"Player bulundu, gidiliyor: {iframe_src}", "DEBUG")
        
        # 2. Adım: Player iframe'ine git
        page.get(iframe_src)
        time.sleep(2)
        
        # 3. Adım: İçerdeki asıl player'ı bul (div#Player iframe)
        nested_iframe = page.ele("css:div#Player iframe")
        if not nested_iframe:
            log("İç player iframe bulunamadı.", "WARNING")
            return None
            
        nested_src = nested_iframe.attr("src")
        if nested_src.startswith("//"): nested_src = "https:" + nested_src
        
        log(f"Kaynak player bulundu: {nested_src}", "DEBUG")
        
        # 4. Adım: Kaynak player'a git ve şifreyi çöz
        page.get(nested_src)
        time.sleep(2)
        
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
        else:
            # Belki şifreli değildir, direkt m3u8 vardır
            m3u8_match = re.search(r'file:\s*"([^"]+\.m3u8[^"]*)"', html)
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
    # DiziBox için User-Agent
    co.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    page = ChromiumPage(co)
    
    # Çerezleri ayarla (Kotlin kodundan)
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
        for i in range(10):
            if "Just a moment" in page.title:
                log(f"Cloudflare bekleniyor... ({i+1}/10)", "WARNING")
                time.sleep(2)
            else:
                break
            
        if "Just a moment" in page.title:
            log("Cloudflare aşılamadı.", "CRITICAL")
            page.quit()
            return

        log("Ana sayfaya erişildi. Linkler taranıyor...", "SUCCESS")
        
        # 2. Bölümleri Topla (GENİŞ KAPSAMLI ARAMA)
        # Belirli bir class'a bağlı kalmadan, URL yapısına göre arıyoruz.
        # DiziBox bölüm linkleri genelde "-izle/" ile biter veya içinde "-sezon-" geçer.
        
        all_links = page.eles("tag:a")
        log(f"Sayfada {len(all_links)} link bulundu. Filtreleniyor...", "INFO")
        
        items = []
        for link in all_links:
            try:
                href = link.attr("href")
                if not href: continue
                
                # Filtre: DiziBox bölüm linki mi?
                # Örnek: https://www.dizibox.live/dizi-adi-1-sezon-1-bolum-izle/
                if "-izle" in href and "-sezon-" in href:
                    
                    # Başlığı bulmaya çalış
                    # Linkin içinde h3 varsa başlık odur, yoksa linkin textidir.
                    # DrissionPage ile elementin iç HTML'ine bakabiliriz.
                    
                    # Linkin kendisi bir kapsayıcı olabilir
                    inner_html = link.html
                    soup = BeautifulSoup(inner_html, 'html.parser')
                    
                    h3 = soup.find("h3")
                    if h3:
                        title = h3.text.strip()
                    else:
                        # Belki resim alt etiketi vardır
                        img = soup.find("img")
                        if img and img.get("alt"):
                            title = img.get("alt")
                        else:
                            # Hiçbiri yoksa linkin metni (bazı temalarda)
                            title = soup.get_text().strip()
                    
                    if not title: continue
                    
                    # Poster (Opsiyonel)
                    img_tag = soup.find("img")
                    poster = ""
                    if img_tag:
                        poster = img_tag.get("data-src") or img_tag.get("src") or ""

                    # URL düzeltme
                    full_url = href
                    if not full_url.startswith("http"):
                        full_url = BASE_URL + full_url
                        
                    # Tekrarı önle
                    if not any(d['url'] == full_url for d in items):
                        items.append({
                            "title": title,
                            "url": full_url,
                            "category": "Son Eklenenler",
                            "poster": poster
                        })
                        
                if len(items) >= 20: break # 20 bölüm yeter
            except: continue
            
        if len(items) == 0:
            log("HİÇ BÖLÜM BULUNAMADI! HTML yapısı değişmiş olabilir.", "CRITICAL")
            # Debug için HTML'in bir kısmını yazdır
            log(f"HTML DUMP (İlk 1000 karakter): {page.html[:1000]}", "DEBUG")
        else:
            log(f"{len(items)} adet bölüm bulundu. Linkler çözülüyor...", "INFO")
            
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
                        
                        # Poster varsa ekle
                        logo_attr = f'tvg-logo="{item["poster"]}"' if item["poster"] else ""
                        
                        f.write(f'#EXTINF:-1 group-title="{item["category"]}" {logo_attr}, {item["title"]}\n')
                        f.write(f"{final_url}\n")
                        log("Eklendi.", "SUCCESS")
                    else:
                        log("Stream bulunamadı.", "WARNING")
                    
                    time.sleep(1)
        else:
            log("Liste boş, dosya oluşturulmadı.", "WARNING")

    except Exception as e:
        log(f"Kritik Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
