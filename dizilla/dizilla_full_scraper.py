import asyncio
import aiohttp
import json
import base64
import re
import os
import sys
from Crypto.Cipher import AES
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

# --- AYARLAR ---
# Eğer site açılmıyorsa güncel adresi tarayıcıdan kontrol edip burayı değiştir:
BASE_URL = "https://dizilla43.com"  # Genelde sayı artar (40 -> 41 -> 42 -> 43)
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 10 
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

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
    async with sem:
        try:
            default_headers = {
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/arsiv", # Kotlin koddaki referer
                "Origin": BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*"
            }
            if headers: default_headers.update(headers)
            
            async with session.request(method, url, data=data, headers=default_headers, timeout=30) as response:
                if response.status == 200:
                    try:
                        return await response.json()
                    except:
                        return await response.text()
                else:
                    # Hata varsa ekrana bas (Debug için çok önemli)
                    # print(f"HATA: {url} -> Status: {response.status}")
                    return None
        except Exception as e:
            # print(f"Bağlantı Hatası: {e}")
            return None

async def scrape_series_page(session, page_num):
    url = f"{BASE_URL}/api/bg/findSeries?releaseYearStart=1900&releaseYearEnd=2026&imdbPointMin=0&imdbPointMax=10&categoryIdsComma=&countryIdsComma=&orderType=date_desc&languageId=-1&currentPage={page_num}&currentPageCount=24"
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
                        poster = item.get("poster_url", "")
                        if poster:
                            poster = poster.replace("images-macellan-online.cdn.ampproject.org/i/s/", "") \
                                           .replace("file.dizilla.club", "file.macellan.online") \
                                           .replace("/f/f/", "/630/910/")
                        
                        category = "Genel"
                        if "kore" in str(item).lower(): category = "Kore Dizileri"
                        elif "anime" in str(item).lower(): category = "Anime"
                        
                        series_list.append({
                            "id": slug,
                            "title": item.get("original_title"),
                            "url": f"{BASE_URL}/{slug}",
                            "poster": poster,
                            "category": category,
                            "episodes": []
                        })
                except:
                    pass
    return series_list

async def process_series(session, series_data):
    try:
        html = await fetch_url(session, series_data["url"])
        if not html or not isinstance(html, str): return series_data
        
        soup = BeautifulSoup(html, 'html.parser')
        episodes_list = []
        season_links = soup.select("div.flex.items-center.flex-wrap.gap-2.mb-4 a")
        
        urls_to_scan = [series_data["url"]] + [
            (l.get("href") if l.get("href").startswith("http") else f"{BASE_URL}{l.get('href')}") 
            for l in season_links
        ]
        urls_to_scan = list(set(urls_to_scan))
        
        for s_url in urls_to_scan:
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
                full_ep_url = ep_href if ep_href.startswith("http") else f"{BASE_URL}{ep_href}"
                ep_name = a_tag.get_text(strip=True)
                
                episodes_list.append({
                    "season": season_num,
                    "name": ep_name,
                    "url": full_ep_url
                })
        
        series_data["episodes"] = episodes_list
        return series_data
    except:
        return series_data

async def main():
    # SSL Hatalarını Yoksay (Cloudflare bypass için yardımcı olabilir)
    connector = aiohttp.TCPConnector(ssl=False)
    
    db = {}
    # Eski DB varsa yükle
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except: pass

    async with aiohttp.ClientSession(connector=connector) as session:
        print(f"Tarama başlıyor: {BASE_URL}")
        
        # 1. İlk sayfa testi (Site erişilebilir mi?)
        test_url = f"{BASE_URL}/api/bg/findSeries?releaseYearStart=2024&currentPage=1&currentPageCount=1"
        test_resp = await fetch_url(session, test_url, method="POST")
        if not test_resp:
            print("!!! HATA: Siteye erişilemiyor veya API yanıt vermiyor (403/404).")
            print("Domain değişmiş olabilir, lütfen BASE_URL ayarını kontrol edin.")
            # Dosyaları boş oluşturup çıkıyoruz ki GIT hata vermesin
            with open(CACHE_FILE, "w", encoding="utf-8") as f: json.dump(db, f)
            with open(OUTPUT_M3U, "w", encoding="utf-8") as f: f.write("#EXTM3U\n")
            return

        # 2. Sayfaları Tara
        tasks = [scrape_series_page(session, i) for i in range(1, 51)] # İlk 50 sayfa yeterli
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Sayfalar Taranıyor"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} yeni dizi bulundu.")
        
        for s in results:
            if s["id"] not in db:
                db[s["id"]] = s
            else:
                db[s["id"]]["title"] = s["title"]

        # 3. Bölümleri Tara
        keys_to_scan = list(db.keys())
        # Demo amaçlı ilk 20 diziyi tarayalım (Hız testi için) - Sonra bu limiti kaldırabilirsin
        # keys_to_scan = keys_to_scan[:20] 
        
        if keys_to_scan:
            print(f"{len(keys_to_scan)} dizi için bölümler kontrol ediliyor...")
            chunk_size = 20
            for i in range(0, len(keys_to_scan), chunk_size):
                chunk = keys_to_scan[i:i+chunk_size]
                batch_tasks = [process_series(session, db[k]) for k in chunk]
                scanned_series = await asyncio.gather(*batch_tasks)
                for s in scanned_series:
                    db[s["id"]] = s
                # Ara kayıt
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=2)

    # 4. M3U Oluştur
    print(f"M3U oluşturuluyor: {len(db)} dizi")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s_id, data in db.items():
            for ep in data.get("episodes", []):
                title = f"{data['title']} - S{ep['season']} {ep['name']}"
                f.write(f'#EXTINF:-1 group-title="{data.get("category","Genel")}" tvg-logo="{data["poster"]}", {title}\n')
                f.write(f"{ep['url']}\n")

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except Exception as e:
        print(f"Genel Hata: {e}")
        # Hata olsa bile dosyaları oluştur
        if not os.path.exists(OUTPUT_M3U): open(OUTPUT_M3U, 'w').close()
        if not os.path.exists(CACHE_FILE): open(CACHE_FILE, 'w').write("{}")
