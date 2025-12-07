import cloudscraper
import re
import base64
import hashlib
import time
import concurrent.futures
import os
from bs4 import BeautifulSoup
from Crypto.Cipher import AES

# --- AYARLAR ---
BASE_URL = "https://www.dizibox.live"
OUTPUT_FILE = "dizibox.m3u"
MAX_WORKERS = 10  # Aynı anda kaç bölüm taranacak (Hız için artırılabilir ama ban riski artar)
MAX_PAGES = 500   # Kaç sayfa taranacağı (Tüm site için çok yüksek sayı verin örn: 5000)

# CloudScraper Kurulumu
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)
# Gerekli çerezler (Sürekli değişebilir)
scraper.cookies.update({
    "LockUser": "true",
    "isTrustedUser": "true",
    "dbxu": "1744054959089"
})

def bytes_to_key(data, salt, output=48):
    """OpenSSL Key Derivation Function"""
    data = data.encode('utf-8')
    data += salt
    key = hashlib.md5(data).digest()
    final_key = key
    while len(final_key) < output:
        key = hashlib.md5(key + data).digest()
        final_key += key
    return final_key[:output]

def decrypt_openssl(passphrase, encrypted_base64):
    """AES Decryption"""
    try:
        encrypted_data = base64.b64decode(encrypted_base64)
        if encrypted_data[:8] != b'Salted__':
            return None
        salt = encrypted_data[8:16]
        cipher_bytes = encrypted_data[16:]
        key_iv = bytes_to_key(passphrase, salt, 48)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(cipher_bytes)
        padding_len = decrypted[-1]
        return decrypted[:-padding_len].decode('utf-8')
    except Exception:
        return None

def resolve_stream(episode_url, referer_url):
    """Tek bir bölümün linkini çözer"""
    try:
        html = scraper.get(episode_url, headers={"Referer": referer_url}).text
        soup = BeautifulSoup(html, 'html.parser')
        
        iframe = soup.select_one("div#video-area iframe")
        if not iframe: return None
        
        iframe_src = iframe.get("src", "").replace("php?v=", "php?wmode=opaque&v=")
        
        # 2. Aşama: Player Iframe
        resp_player = scraper.get(iframe_src, headers={"Referer": episode_url})
        soup_player = BeautifulSoup(resp_player.text, 'html.parser')
        embed_iframe = soup_player.select_one("div#Player iframe")
        
        if not embed_iframe: return None
        embed_url = embed_iframe.get("src", "")
        
        if "vidmoly" in embed_url:
            embed_url = embed_url.replace("vidmoly.me", "vidmoly.net")
            if "/embed/" in embed_url and "/sheila/" not in embed_url:
                embed_url = embed_url.replace("/embed/", "/embed/sheila/")
        
        # 3. Aşama: Decryption
        resp_embed = scraper.get(embed_url, headers={"Referer": iframe_src})
        content = resp_embed.text
        
        # Direkt M3U8 var mı?
        if "dbx.molystream" in embed_url:
            for line in content.splitlines():
                if line.startswith("http"): return line

        # Şifreli mi?
        crypt_data = re.search(r'CryptoJS\.AES\.decrypt\(\"(.*?)\",\"', content)
        crypt_pass = re.search(r'\",\"(.*?)\"\);', content)
        
        if crypt_data and crypt_pass:
            decrypted = decrypt_openssl(crypt_pass.group(1), crypt_data.group(1))
            if decrypted:
                match = re.search(r"file:\s*'(.*?)'", decrypted) or re.search(r'file:\s*"(.*?)"', decrypted)
                if match: return match.group(1)
                
    except Exception as e:
        print(f"Hata ({episode_url}): {e}")
    return None

def process_episode(args):
    """Thread içinde çalışacak fonksiyon"""
    ep_name, ep_url, poster, category, series_name = args
    stream_url = resolve_stream(ep_url, BASE_URL)
    if stream_url:
        return f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster}", {series_name} - {ep_name}\n{stream_url}'
    return None

def main():
    print("DiziBox Tarayıcı Başlatıldı (Multi-Thread)...")
    
    # Kategori URL'leri (Hepsini taramak için burayı genişletin)
    categories = [
        ("Aksiyon", "aksiyon"), ("Komedi", "komedi"), 
        ("Bilim Kurgu", "bilimkurgu"), ("Dram", "drama")
    ]
    
    all_m3u_lines = ["#EXTM3U"]
    
    for cat_name, cat_slug in categories:
        print(f"--- Kategori: {cat_name} ---")
        for page in range(1, MAX_PAGES + 1):
            url = f"{BASE_URL}/dizi-arsivi/page/{page}/?tur[0]={cat_slug}&yil&imdb"
            print(f"Sayfa Taranıyor: {page} ({url})")
            
            resp = scraper.get(url)
            if resp.status_code != 200:
                print("Sayfa sonuna gelindi veya engellendi.")
                break
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            articles = soup.select("article.detailed-article")
            
            if not articles: break
            
            episode_tasks = []
            
            for art in articles:
                title_tag = art.select_one("h3 a")
                img_tag = art.select_one("img")
                if not title_tag: continue
                
                series_name = title_tag.text.strip()
                series_href = title_tag['href']
                poster = img_tag.get('data-src') or img_tag.get('src') or ""
                
                # Dizi sayfasına git ve son bölümü veya bölümleri al
                # Hız kazanmak için sadece listelenen son bölümleri alıyoruz
                # Eğer TÜM arşiv isteniyorsa dizi içine girip tüm sezonları döngüye sokmak gerekir
                # Bu örnek ana sayfadaki listeleme mantığıyla çalışır.
                
                # Detaylı tarama için dizi sayfasına gir:
                s_resp = scraper.get(series_href)
                if s_resp.status_code == 200:
                    s_soup = BeautifulSoup(s_resp.text, 'html.parser')
                    episodes = s_soup.select("article.grid-box div.post-title a")
                    
                    for ep in episodes:
                        ep_title = ep.text.strip()
                        ep_href = ep['href']
                        # Listeye ekle (Daha sonra thread ile işlenecek)
                        episode_tasks.append((ep_title, ep_href, poster, cat_name, series_name))

            # ThreadPool ile sayfadaki tüm bölümleri aynı anda çöz
            if episode_tasks:
                print(f"  > {len(episode_tasks)} bölüm işleniyor...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    results = list(executor.map(process_episode, episode_tasks))
                
                # Sonuçları kaydet
                for res in results:
                    if res:
                        all_m3u_lines.append(res)
            
            # M3U Dosyasını her sayfada güncelle (Crash durumuna karşı)
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(all_m3u_lines))

if __name__ == "__main__":
    main()
