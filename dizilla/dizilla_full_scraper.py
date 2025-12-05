import asyncio
import aiohttp
import json
import base64
import re
import os
import sys
import cloudscraper
from Crypto.Cipher import AES
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

# --- AYARLAR ---
# Bu ana domaindir, script buradan güncel adrese (örn: dizilla44.com) yönlenecek.
START_URL = "https://dizilla.club" 
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 5 # Cloudflare varken sayıyı düşürmek daha güvenlidir
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global değişken (Dinamik olarak güncellenecek)
CURRENT_BASE_URL = ""

def get_cloudflare_tokens():
    """
    Cloudscraper kullanarak Cloudflare engelini aşar,
    güncel domaini bulur ve cookie'leri döner.
    """
    print("Cloudflare koruması aşılıyor ve güncel adres aranıyor...")
    try:
        # Browser gibi davranacak scraper oluştur
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        
        # Siteye ilk isteği at
        resp = scraper.get(START_URL, timeout=15)
        
        # Yönlenilen son adresi al (Örn: https://dizilla44.com/)
        final_url = resp.url.rstrip("/")
        cookies = scraper.cookies.get_dict()
        user_agent = scraper.headers.get("User-Agent")
        
        print(f"✅ Cloudflare aşıldı! Güncel Adres: {final_url}")
        return final_url, cookies, user_agent
        
    except Exception as e:
        print(f"❌ Cloudflare hatası: {e}")
        return None, None, None

def decrypt_dizilla_response(encrypted_str):
    try:
        if not encrypted_str: return None
        iv = bytes(16)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
        decoded_b64 = base64.b64decode(encrypted_str)
        decrypted = cipher.decrypt(decoded_b64)
        padding_len = decrypted[-1]
        return decrypted[:-padding_len].decode('utf-8')
    except Exception as e:
        return None

async def fetch_url(session, url, method="GET", data=None, headers=None):
    async with SEM:
        try:
            # Cloudscraper'dan gelen headerları koru, üzerine ekle
            req_headers = {
                "Referer": f"{CURRENT_BASE_URL}/arsiv",
                "Origin": CURRENT_BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*"
            }
            if headers: req_headers.update(headers)
            
            async with session.request(method, url, data=data, headers=req_headers, timeout=30) as response:
                if response.status == 200:
                    try:
                        return await response.json()
                    except:
                        return await response.text()
                else:
                    # Debug için status code
                    # print(f"HTTP {response.status}: {url}") 
                    return None
        except Exception:
            return None

async def scrape_series_page(session, page_num):
    url = f"{CURRENT_BASE_URL}/api/bg/findSeries?releaseYearStart=1900&releaseYearEnd=2026&imdbPointMin=0&imdbPointMax=10&categoryIdsComma=&countryIdsComma=&orderType=date_desc&languageId=-1&currentPage={page_num}&currentPageCount=24"
    resp = await fetch_url(session, url, method="POST")
    series_list = []
    
    if isinstance(resp, dict):
        enc = resp.get("response")
        if enc:
            dec = decrypt_dizilla_response(enc)
            if dec:
                try:
                    data = json.loads(dec)
                    items = data.get("items") or data.get("result") or []
                    for item in items:
                        slug = item.get("used_slug")
                        if not slug: continue
                        
                        # Poster işlemleri
                        poster = item.get("poster_url", "")
                        if poster:
                            poster = poster.replace("images-macellan-online.cdn.ampproject.org/i/s/", "") \
                                           .replace("file.dizilla.club", "file.macellan.online") \
                                           .replace("/f/f/", "/630/910/")
                        
                        category = "Genel"
                        title = item.get("original_title", "Bilinmeyen")
                        if "kore" in str(item).lower(): category = "Kore Dizileri"
                        elif "anime" in str(item).lower(): category = "Anime"
                        
                        series_list.append({
                            "id": slug,
                            "title": title,
                            "url": f"{CURRENT_BASE_URL}/{slug}",
                            "poster": poster,
                            "category": category,
                            "episodes": []
                        })
                except:
                    pass
    return series_list

async def process_series(session, series_data):
    try:
        # URL'yi güncel domain ile güncelle (db'den eski domain gelirse diye)
        slug = series_data["id"]
        current_series_url = f"{CURRENT_BASE_URL}/{slug}"
        
        html = await fetch_url(session, current_series_url)
        if not html or not isinstance(html, str): return series_data
        
        soup = BeautifulSoup(html, 'html.parser')
        episodes_list = []
        season_links = soup.select("div.flex.items-center.flex-wrap.gap-2.mb-4 a")
        
        urls_to_scan = [current_series_url] + [
            (l.get("href") if l.get("href").startswith("http") else f"{CURRENT_BASE_URL}{l.get('href')}") 
            for l in season_links
        ]
        urls_to_scan = list(set(urls_to_scan))
        
        for s_url in urls_to_scan:
            # Domain kontrolü ve düzeltme
            if not s_url.startswith(CURRENT_BASE_URL):
                # Eğer link eski domainde kaldıysa güncelle
                path = s_url.split("/", 3)[-1] if s_url.startswith("http") else s_url
                s_url = f"{CURRENT_BASE_URL}/{path}"

            s_html = await fetch_url(session, s_url)
            if not s_html or not isinstance(s_html, str): continue
            
            s_soup = BeautifulSoup(s_html, 'html.parser')
            season_match = re.search(r'-(\d+)-sezon', s_url)
            season_num = season_match.group(1) if season_match else "1"
            
            ep_divs = s_soup.select("div.episodes div.cursor-pointer")
            for ep_div in ep_divs:
                a_tag = ep_div.select_one("a")
                if not a_tag: continue
                
                ep_href = a_tag.get("href")
                if not ep_href: continue
                
                full_ep_url = ep_href if ep_href.startswith("http") else f"{CURRENT_BASE_URL}{ep_href}"
                ep_name = a_tag.get_text(strip=True)
                
                episodes_list.append({
                    "season": season_num,
                    "name": ep_name,
                    "url": full_ep_url
                })
        
        # Sadece yeni veri bulduysak güncelle, yoksa eskisini koru
        if episodes_list:
            series_data["episodes"] = episodes_list
            
        return series_data
    except:
        return series_data

async def main():
    global CURRENT_BASE_URL
    
    # 1. Cloudscraper ile yetki al
    final_url, cookies, ua = get_cloudflare_tokens()
    
    if not final_url:
        print("!!! Kritik Hata: Siteye girilemedi. Çıkılıyor.")
        # Git hatası olmasın diye boş dosyaları oluştur
        if not os.path.exists(OUTPUT_M3U): open(OUTPUT_M3U, 'w').close()
        if not os.path.exists(CACHE_FILE): open(CACHE_FILE, 'w').write("{}")
        return

    CURRENT_BASE_URL = final_url
    
    # SSL hatalarını yoksay
    connector = aiohttp.TCPConnector(ssl=False)
    
    # Cloudscraper'dan aldığımız cookies ve header'ı aiohttp'ye veriyoruz
    headers = {"User-Agent": ua}
    
    db = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except: pass

    async with aiohttp.ClientSession(connector=connector, cookies=cookies, headers=headers) as session:
        print(f"Oturum açıldı. Tarama başlıyor: {CURRENT_BASE_URL}")
        
        # 2. Sayfaları Tara
        # Deneme amaçlı 20 sayfa. Sorun yoksa 150 yap.
        tasks = [scrape_series_page(session, i) for i in range(1, 21)] 
        
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Dizi Listesi"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} dizi bulundu.")
        
        for s in results:
            # URL'leri güncel domain ile revize et
            s["url"] = f"{CURRENT_BASE_URL}/{s['id']}"
            
            if s["id"] not in db:
                db[s["id"]] = s
            else:
                db[s["id"]]["title"] = s["title"]
                db[s["id"]]["url"] = s["url"] # URL güncelle

        # 3. Bölümleri Tara (Asıl yoğun kısım)
        keys_to_scan = list(db.keys())
        
        # Demo: İlk çalıştırmada çok uzun sürmemesi için limit koyabilirsin
        # keys_to_scan = keys_to_scan[:50] 
        
        if keys_to_scan:
            print(f"{len(keys_to_scan)} dizi için bölüm bilgileri taranıyor...")
            chunk_size = 10 # Cloudflare varken küçük parçalar daha iyidir
            for i in range(0, len(keys_to_scan), chunk_size):
                chunk = keys_to_scan[i:i+chunk_size]
                batch_tasks = [process_series(session, db[k]) for k in chunk]
                scanned_series = await asyncio.gather(*batch_tasks)
                
                for s in scanned_series:
                    db[s["id"]] = s
                
                # İlerleme kaydı
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=2)

    # 4. M3U Oluştur
    print(f"M3U oluşturuluyor... ({len(db)} kayıt)")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s_id, data in db.items():
            episodes = data.get("episodes", [])
            for ep in episodes:
                title = f"{data['title']} - S{ep['season']} {ep['name']}"
                cat = data.get("category", "Genel")
                poster = data.get("poster", "")
                url = ep['url']
                
                f.write(f'#EXTINF:-1 group-title="{cat}" tvg-logo="{poster}", {title}\n')
                f.write(f"{url}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
