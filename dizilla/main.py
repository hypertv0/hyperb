import os
import json
import base64
import re
import time
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup
import cloudscraper

# --- AYARLAR ---
BASE_URL = "https://dizilla40.com"
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"  # Kotlin kodundan alındı
AES_IV = bytes([0] * 16)  # Kotlin'de ByteArray(16) varsayılan olarak 0'dır
OUTPUT_FILE = "dizilla.m3u"

# True yaparsan tüm arşivi tarar (Çok uzun sürer!), False ise sadece son eklenenleri alır.
SCRAPE_ALL_ARCHIVE = False 
MAX_PAGES = 5 # Eğer arşiv tarıyorsan kaç sayfa tarasın? (Sınırsız için -1 yap)

# Cloudscraper, Cloudflare korumasını aşmak için requests yerine kullanılır
scraper = cloudscraper.create_scraper()

def decrypt_dizilla_response(encrypted_data):
    """Dizilla'nın AES-256-CBC şifrelemesini çözer."""
    try:
        if not encrypted_data:
            return None
        
        # Base64 decode
        encrypted_bytes = base64.b64decode(encrypted_data)
        
        # AES Decryption
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        print(f"Şifre çözme hatası: {e}")
        return None

def get_headers(referer=BASE_URL):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": BASE_URL
    }

def extract_contentx(url, referer):
    """ContentX kaynağından m3u8 linkini ayıklar (Kotlin kodundaki mantık)."""
    try:
        print(f"   -> ContentX işleniyor: {url}")
        resp = scraper.get(url, headers=get_headers(referer))
        if resp.status_code != 200:
            return None
        
        text = resp.text
        
        # window.openPlayer('...') bul
        match = re.search(r"window\.openPlayer\('([^']+)'", text)
        if not match:
            return None
        
        extract_id = match.group(1)
        
        # Source2.php isteği
        source_url = f"https://contentx.me/source2.php?v={extract_id}"
        vid_resp = scraper.get(source_url, headers=get_headers(url))
        
        # file: "..." bul
        vid_match = re.search(r'file":"([^"]+)"', vid_resp.text)
        if vid_match:
            m3u_link = vid_match.group(1).replace("\\", "")
            return m3u_link
            
    except Exception as e:
        print(f"   -> ContentX Hatası: {e}")
    return None

def get_stream_url(episode_url):
    """Bölüm sayfasından iframe'i ve ardından stream linkini bulur."""
    try:
        resp = scraper.get(episode_url, headers=get_headers())
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # __NEXT_DATA__ scriptini bul
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            return None
            
        data = json.loads(script.string)
        secure_data_enc = data.get("props", {}).get("pageProps", {}).get("secureData", "")
        
        if not secure_data_enc:
            return None
            
        # Şifreli veriyi çöz
        decrypted_json = decrypt_dizilla_response(secure_data_enc)
        if not decrypted_json:
            return None
            
        page_data = json.loads(decrypted_json)
        
        # Kaynakları bul
        sources = page_data.get("RelatedResults", {}).get("getEpisodeSources", {}).get("result", [])
        if not sources:
            return None
            
        # İlk kaynağı al (Genelde en hızlısı)
        source_content = sources[0].get("source_content", "")
        
        # Iframe src'sini bul
        iframe_soup = BeautifulSoup(source_content, 'html.parser')
        iframe = iframe_soup.find("iframe")
        if not iframe:
            return None
            
        iframe_src = iframe.get("src")
        if not iframe_src:
            return None
            
        # URL düzeltmeleri
        if iframe_src.startswith("//"):
            iframe_src = "https:" + iframe_src
            
        # ContentX ise ayıkla
        if "contentx.me" in iframe_src or "hotlinger" in iframe_src:
            return extract_contentx(iframe_src, episode_url)
        
        # Diğer kaynaklar için iframe'i döndür (veya buraya diğer extractorlar eklenebilir)
        return iframe_src

    except Exception as e:
        print(f"Stream bulma hatası ({episode_url}): {e}")
        return None

def scrape_latest_episodes():
    """Ana sayfadaki son eklenen bölümleri çeker."""
    print("Son eklenen bölümler taranıyor...")
    url = f"{BASE_URL}/tum-bolumler"
    resp = scraper.get(url, headers=get_headers())
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    items = []
    
    # Seçiciler site tasarımına göre değişebilir, Kotlin kodundaki mantığa göre:
    # div.col-span-3 a -> sonBolumler()
    cards = soup.select("div.col-span-3 a")
    
    for card in cards:
        try:
            href = card.get("href")
            if not href: continue
            
            full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
            
            title_tag = card.select_one("h2")
            ep_tag = card.select_one("div.opacity-80")
            
            title = title_tag.text.strip() if title_tag else "Bilinmeyen Dizi"
            ep_info = ep_tag.text.strip() if ep_tag else ""
            
            # "2. Sezon 5. Bölüm" -> "S2 E5" formatına çevir
            ep_info_fmt = ep_info.replace(". Sezon ", "x").replace(". Bölüm", "")
            
            full_title = f"{title} - {ep_info_fmt}"
            
            items.append({
                "title": full_title,
                "url": full_url,
                "category": "Yeni Eklenenler"
            })
        except Exception as e:
            continue
            
    return items

def scrape_archive(page=1):
    """API üzerinden arşiv taraması yapar."""
    print(f"Arşiv taranıyor... Sayfa: {page}")
    api_url = f"{BASE_URL}/api/bg/findSeries"
    
    # Kotlin kodundaki parametreler
    params = {
        "releaseYearStart": "1900",
        "releaseYearEnd": "2025",
        "imdbPointMin": "5",
        "imdbPointMax": "10",
        "orderType": "date_desc",
        "languageId": "-1",
        "currentPage": str(page),
        "currentPageCount": "24",
        "queryStr": "",
        "categoryIdsComma": "", # Kategori ID'leri buraya eklenebilir
        "countryIdsComma": "",
        "categorySlugsComma": "",
        "countryCodesComma": ""
    }
    
    try:
        resp = scraper.post(api_url, params=params, headers=get_headers(f"{BASE_URL}/arsiv"))
        data = resp.json()
        
        decrypted = decrypt_dizilla_response(data.get("response"))
        if not decrypted:
            return []
            
        json_data = json.loads(decrypted)
        results = json_data.get("items", []) or json_data.get("result", [])
        
        items = []
        for res in results:
            slug = res.get("used_slug")
            title = res.get("original_title")
            if slug and title:
                # Burası sadece dizi sayfasını verir, bölümleri almak için içine girmek gerekir.
                # Bu işlem çok uzun süreceği için burada sadece dizi linkini bırakıyorum.
                # Gelişmiş versiyonda dizi sayfasına girip bölümleri looplamak gerekir.
                items.append({
                    "title": title,
                    "url": f"{BASE_URL}/{slug}",
                    "category": "Arşiv"
                })
        return items
        
    except Exception as e:
        print(f"Arşiv tarama hatası: {e}")
        return []

def generate_m3u(playlist_items):
    print(f"M3U oluşturuluyor... Toplam {len(playlist_items)} içerik.")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for item in playlist_items:
            # Stream linkini çöz
            stream_url = get_stream_url(item["url"])
            
            if stream_url:
                # Tivimate vb. için User-Agent ekleyelim (Pipe ile)
                if ".m3u8" in stream_url:
                    final_url = f"{stream_url}|User-Agent={get_headers()['User-Agent']}"
                else:
                    final_url = stream_url
                
                f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                f.write(f"{final_url}\n")
            else:
                print(f"Link bulunamadı: {item['title']}")
            
            # Sunucuyu yormamak için bekleme
            time.sleep(0.5)

def main():
    all_content = []
    
    # 1. Son Eklenenleri Çek
    latest = scrape_latest_episodes()
    all_content.extend(latest)
    
    # 2. Arşivi Çek (Opsiyonel)
    if SCRAPE_ALL_ARCHIVE:
        page = 1
        while True:
            archive_items = scrape_archive(page)
            if not archive_items:
                break
            
            # Arşivden gelenler dizi sayfasıdır, bölümleri çekmek için ekstra mantık gerekir.
            # Bu örnekte sadece son eklenenler tam çalışır.
            # all_content.extend(archive_items) 
            
            page += 1
            if MAX_PAGES != -1 and page > MAX_PAGES:
                break
            time.sleep(1)

    # 3. M3U Oluştur
    generate_m3u(all_content)
    print("İşlem tamamlandı.")

if __name__ == "__main__":
    main()
