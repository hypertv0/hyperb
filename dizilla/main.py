import undetected_chromedriver as uc
import re
import base64
import hashlib
import time
import shutil
import subprocess
import os
import random
from Crypto.Cipher import AES
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
SITEMAP_URL = "https://www.dizibox.live/tvseries-sitemap.xml"
OUTPUT_FILE = "dizibox.m3u"
# Hepsini çekmek çok uzun sürer, test için limit koyun (0 = Limitsiz)
MAX_SERIES = 5
MAX_EPISODES_PER_SERIES = 1 

def get_chrome_version():
    try:
        output = subprocess.check_output(['google-chrome', '--version'], stderr=subprocess.STDOUT)
        version = re.search(r'Chrome (\d+)', output.decode('utf-8')).group(1)
        return int(version)
    except:
        return None

def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    
    chrome_path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_path: options.binary_location = chrome_path

    version = get_chrome_version()
    try:
        driver = uc.Chrome(options=options, version_main=version) if version else uc.Chrome(options=options)
    except:
        driver = uc.Chrome(options=options)
    return driver

# --- ŞİFRE ÇÖZME ---
def bytes_to_key(data, salt, output=48):
    data = data.encode('utf-8') + salt
    key = hashlib.md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = hashlib.md5(key + data).digest()
        final_key += key
    return final_key[:output]

def decrypt_openssl(passphrase, encrypted_base64):
    try:
        encrypted_data = base64.b64decode(encrypted_base64)
        if encrypted_data[:8] != b'Salted__': return None
        salt = encrypted_data[8:16]
        cipher_bytes = encrypted_data[16:]
        key_iv = bytes_to_key(passphrase, salt, 48)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(cipher_bytes)
        return decrypted[:-decrypted[-1]].decode('utf-8')
    except: return None

def resolve_stream(driver, episode_url):
    """Link çözücü (Vidmoly/Sheila/M3U8)"""
    try:
        driver.get(episode_url)
        # Sayfanın yüklenmesini biraz bekle
        time.sleep(3)
        
        # Sayfa kaynağında iframe ara
        page_source = driver.page_source
        
        # 1. Kaynaktaki iframe linklerini Regex ile bul
        # Örn: src="https://....king.php?v=..."
        iframes = re.findall(r'src=["\'](https?://[^"\']*?(?:king\.php|drive|trkv)[^"\']*?)["\']', page_source)
        
        target_iframe = None
        for frame in iframes:
            if "dizibox" in frame or "king" in frame:
                target_iframe = frame.replace("php?v=", "php?wmode=opaque&v=")
                break
        
        if not target_iframe: return None

        # 2. Iframe'e git
        driver.get(target_iframe)
        time.sleep(1.5)
        
        # 3. Embed linkini bul (Vidmoly/Sheila)
        embed_src = None
        # Regex ile embed linkini avla
        embeds = re.findall(r'src=["\'](https?://[^"\']*?(?:vidmoly|embed|sheila)[^"\']*?)["\']', driver.page_source)
        if embeds:
            embed_src = embeds[0]
            
        if not embed_src: return None
            
        if "vidmoly" in embed_src:
            embed_src = embed_src.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_src and "/sheila/" not in embed_src:
                embed_src = embed_src.replace("/embed/", "/embed/sheila/")
        
        # 4. Şifre çözme
        driver.get(embed_src)
        time.sleep(1)
        src_code = driver.page_source
        
        # M3U8 var mı?
        m3u_match = re.search(r'(https?://[^\s<"]+\.m3u8[^\s<"]*)', src_code)
        if m3u_match: return m3u_match.group(1)

        # Şifreli veri
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', src_code)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', src_code)
        
        if crypt_data and crypt_pass:
            dec = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if dec:
                file_match = re.search(r"file:\s*'(.*?)'", dec) or re.search(r'file:\s*"(.*?)"', dec)
                if file_match: return file_match.group(1)

    except Exception as e:
        print(f"    Hata: {e}")
    return None

def find_links_with_regex(html_content, pattern):
    """HTML yapısına bakmadan linkleri regex ile söker"""
    links = re.findall(pattern, html_content)
    # Tekrarları temizle
    return list(set(links))

def main():
    print("DiziBox Regex-Brute-Force Modu Başlatılıyor...")
    driver = get_driver()
    all_m3u_lines = ["#EXTM3U"]
    
    try:
        # 1. Site Haritasını (Sitemap) Zorla
        # Sitemap genellikle bot koruması daha düşüktür ve TÜM dizileri içerir.
        print(f"Sitemap deneniyor: {SITEMAP_URL}")
        driver.get(SITEMAP_URL)
        time.sleep(5)
        
        page_source = driver.page_source
        
        # Cloudflare Kontrolü
        if "Just a moment" in driver.title or "Cloudflare" in driver.title:
            print("⚠️ Cloudflare tespit edildi. Bekleniyor...")
            time.sleep(10)
            page_source = driver.page_source # Güncelle
            
        print(f"Sayfa Başlığı: {driver.title}")
        
        # 2. Dizi Linklerini Regex ile Çek
        # Kalıp: https://www.dizibox.live/diziler/dizi-adi/
        series_links = find_links_with_regex(page_source, r'(https://www.dizibox\.live/diziler/[\w-]+/?)')
        
        # Eğer Sitemap'ten gelmezse Ana Sayfadan dene
        if not series_links:
            print("Sitemap boş döndü, Ana Sayfaya geçiliyor...")
            driver.get(BASE_URL)
            time.sleep(7)
            page_source = driver.page_source
            series_links = find_links_with_regex(page_source, r'(https://www.dizibox\.live/diziler/[\w-]+/?)')

        if not series_links:
            print("❌ HİÇBİR DİZİ LİNKİ BULUNAMADI.")
            print("Sayfa içeriğinden örnek:\n" + page_source[:500])
            return

        print(f"✅ Toplam {len(series_links)} dizi linki bulundu.")
        
        # Limit uygula (Test için)
        target_series = series_links
        if MAX_SERIES > 0:
            target_series = series_links[:MAX_SERIES]
            print(f"Hız için sadece ilk {MAX_SERIES} dizi işlenecek.")

        # 3. Dizileri Gez
        for i, s_url in enumerate(target_series):
            # URL'den dizi adını çıkar
            series_name = s_url.strip('/').split('/')[-1].replace('-', ' ').title()
            print(f"\n[{i+1}/{len(target_series)}] Dizi: {series_name}")
            
            try:
                driver.get(s_url)
                time.sleep(3)
                s_source = driver.page_source
                
                # Poster Bul (Basit Regex)
                poster_match = re.search(r'src=["\']([^"\']+\.jpg)["\']', s_source)
                poster_url = poster_match.group(1) if poster_match else ""
                
                # Bölüm Linklerini Regex ile Çek
                # Kalıp: https://www.dizibox.live/dizi-adi-1-sezon-1-bolum-izle/
                ep_links = find_links_with_regex(s_source, r'(https://www.dizibox\.live/[\w-]+-\d+-sezon-\d+-bolum-izle/?)')
                
                if not ep_links:
                    print("  ⚠️ Bölüm linki bulunamadı.")
                    continue
                    
                # Bölümleri sırala (İsteğe bağlı)
                ep_links.sort()
                
                # Limit uygula
                target_eps = ep_links
                if MAX_EPISODES_PER_SERIES > 0:
                    target_eps = ep_links[:MAX_EPISODES_PER_SERIES]
                
                print(f"  > {len(ep_links)} bölümden {len(target_eps)} tanesi işlenecek.")

                for ep_url in target_eps:
                    # Başlık üret
                    ep_slug = ep_url.strip('/').split('/')[-1]
                    ep_name = ep_slug.replace(series_name.lower().replace(' ', '-'), '').replace('-', ' ').strip().title()
                    full_title = f"{series_name} - {ep_name}"
                    
                    print(f"    İşleniyor: {full_title}")
                    
                    stream_url = resolve_stream(driver, ep_url)
                    
                    if stream_url:
                        print(f"    ✅ LİNK: {stream_url[:40]}...")
                        line = f'#EXTINF:-1 group-title="Diziler" tvg-logo="{poster_url}", {full_title}\n{stream_url}'
                        all_m3u_lines.append(line)
                        
                        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                            f.write("\n".join(all_m3u_lines))
                    else:
                        pass
                        # print("    ❌ Stream yok.")

            except Exception as e:
                print(f"  Hata: {e}")

    except Exception as e:
        print(f"Genel Hata: {e}")
    finally:
        driver.quit()
        print("\nİşlem Tamamlandı.")

if __name__ == "__main__":
    main()
