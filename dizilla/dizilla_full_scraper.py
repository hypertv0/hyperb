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
# Bu aralıkta tarama yapacak. Site şu an 40'larda olduğu için 38-60 arası ideal.
DOMAIN_RANGE_START = 38
DOMAIN_RANGE_END = 60
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 5 
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global değişken (Kod bulup buraya atayacak)
CURRENT_BASE_URL = ""

def decrypt_dizilla_response(encrypted_str):
    """AES Şifre Çözücü"""
    try:
        if not encrypted_str: return None
        iv = bytes(16)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, iv)
        decoded_b64 = base64.b64decode(encrypted_str)
        decrypted = cipher.decrypt(decoded_b64)
        padding_len = decrypted[-1]
        return decrypted[:-padding_len].decode('utf-8')
    except Exception:
        return None

def find_active_domain():
    """
    Doğru API adresini bulmak için dizillaXX.com adreslerini dener.
    Sadece sayfası açılanı değil, API'si çalışanı seçer.
    """
    print("🔍 Güncel ve çalışan Dizilla domaini aranıyor...")
    
    # Gerçek bir tarayıcı gibi davran
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    
    # Denenecekler listesi (Önce bilinen yönlendirme, sonra numaralılar)
    candidates = ["https://dizilla.club"] 
    # 40, 41, 42... şeklinde ekle
    for i in range(DOMAIN_RANGE_START, DOMAIN_RANGE_END):
        candidates.append(f"https://dizilla{i}.com")

    for domain in candidates:
        try:
            # Önce domainin kendisine bir ping atalım (Hızlı eleme)
            try:
                head = scraper.head(domain, timeout=5)
                if head.status_code >= 400 and head.status_code != 403:
                    print(f" ❌ {domain} (Ulaşılamıyor)")
                    continue
            except:
                pass

            # Şimdi ASIL test: API isteği at
            # Eğer bu istek dönerse, site kesinlikle çalışıyordur.
            api_url = f"{domain}/api/bg/findSeries?releaseYearStart=2024&currentPage=1&currentPageCount=1"
            headers = {
                "Referer": f"{domain}/arsiv",
                "X-Requested-With": "XMLHttpRequest"
            }
            
            # print(f"Testing API: {domain} ...", end="")
            api_resp = scraper.post(api_url, headers=headers, timeout=10)
            
            if api_resp.status_code == 200:
                json_resp = api_resp.json()
                # Şifreli yanıtı kontrol et
                if "response" in json_resp:
                    dec = decrypt_dizilla_response(json_resp["response"])
                    if dec and ("items" in dec or "result" in dec):
                        print(f"\n ✅ BULUNDU! Güncel Adres: {domain}")
                        
                        # Cloudflare cookie'lerini alıp dön
                        cookies = scraper.cookies.get_dict()
                        ua = scraper.headers.get("User-Agent")
                        return domain, cookies, ua
            
            # print(" (Başarısız)")
        except Exception as e:
            # Hata varsa (Timeout, Connection Error) geç
            pass

    return None, None, None

async def fetch_url(session, url, method="GET", data=None, headers=None):
    async with SEM:
        try:
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
                return None
        except Exception:
            return None

async def scrape_series_page(session, page_num):
    url = f"{CURRENT_BASE_URL}/api/bg/findSeries?releaseYearStart=1900&releaseYearEnd=2026&imdbPointMin=0&imdbPointMax=10&categoryIdsComma=&countryIdsComma=&orderType=date_desc&languageId=-1&currentPage={page_num}&currentPageCount=24"
    
    resp = await fetch_url(session, url, method="POST")
    series_list = []
    
    if isinstance(resp, dict):
        enc = resp.get("response")
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
                        if not poster.startswith("http"):
                             poster = f"https://file.macellan.online/{poster.lstrip('/')}" if "macellan" not in poster else poster

                    category = "Dizi"
                    title = item.get("original_title") or item.get("title") or "Bilinmeyen"
                    
                    if "kore" in str(item).lower(): category = "Kore Dizileri"
                    elif "anime" in str(item).lower(): category = "Anime"
                    
                    full_url = f"{CURRENT_BASE_URL}/{slug}"
                    
                    series_list.append({
                        "id": slug,
                        "title": title,
                        "url": full_url,
                        "poster": poster,
                        "category": category,
                        "episodes": []
                    })
            except Exception:
                pass
    return series_list

async def process_series(session, series_data):
    try:
        slug = series_data["id"]
        current_series_url = f"{CURRENT_BASE_URL}/{slug}"
        
        html = await fetch_url(session, current_series_url)
        if not html or not isinstance(html, str): return series_data
        
        soup = BeautifulSoup(html, 'html.parser')
        episodes_list = []
        
        season_links = soup.select("div.flex.items-center.flex-wrap.gap-2.mb-4 a")
        
        urls_to_scan = [current_series_url]
        for l in season_links:
            href = l.get("href")
            if href:
                if href.startswith("http"):
                    if CURRENT_BASE_URL not in href:
                        # Domain farklıysa linki onar
                        path = href.split("/", 3)[-1]
                        urls_to_scan.append(f"{CURRENT_BASE_URL}/{path}")
                    else:
                        urls_to_scan.append(href)
                else:
                    urls_to_scan.append(f"{CURRENT_BASE_URL}{href}")
        
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
                if not ep_href: continue
                
                if ep_href.startswith("http"):
                    if CURRENT_BASE_URL not in ep_href:
                        path = ep_href.split("/", 3)[-1]
                        full_ep_url = f"{CURRENT_BASE_URL}/{path}"
                    else:
                        full_ep_url = ep_href
                else:
                    full_ep_url = f"{CURRENT_BASE_URL}{ep_href}"

                ep_name = a_tag.get_text(strip=True)
                
                episodes_list.append({
                    "season": season_num,
                    "name": ep_name,
                    "url": full_ep_url
                })
        
        if episodes_list:
            series_data["episodes"] = episodes_list
            
        return series_data
    except Exception:
        return series_data

async def main():
    global CURRENT_BASE_URL
    
    # 1. Otomatik Domain Bulucu
    active_domain, cookies, ua = find_active_domain()
    
    if not active_domain:
        print("!!! KRİTİK: Çalışan domain bulunamadı. Lütfen DOMAIN_RANGE ayarlarını kontrol et.")
        if not os.path.exists(OUTPUT_M3U): open(OUTPUT_M3U, 'w').close()
        if not os.path.exists(CACHE_FILE): open(CACHE_FILE, 'w').write("{}")
        return

    CURRENT_BASE_URL = active_domain
    
    connector = aiohttp.TCPConnector(ssl=False)
    headers = {"User-Agent": ua}
    
    db = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except: pass

    async with aiohttp.ClientSession(connector=connector, cookies=cookies, headers=headers) as session:
        print(f"Veri çekme işlemi başladı: {CURRENT_BASE_URL}")
        
        # İlk etapta son 50 sayfayı tara (Hız ve güncellik için)
        tasks = [scrape_series_page(session, i) for i in range(1, 51)]
        
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Dizi Listesi"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} dizi bulundu.")
        
        new_items_count = 0
        for s in results:
            s["url"] = f"{CURRENT_BASE_URL}/{s['id']}" # URL'yi tazele
            if s["id"] not in db:
                db[s["id"]] = s
                new_items_count += 1
            else:
                # Var olanı güncelle ama bölümleri koru (şimdilik)
                db[s["id"]]["title"] = s["title"]
                db[s["id"]]["url"] = s["url"]
        
        print(f"Veritabanına {new_items_count} yeni dizi eklendi.")

        # Bölümleri Tara
        keys_to_scan = list(db.keys())
        # Demo: İlk test için 50 dizi. Sorunsuz çalışırsa bu satırı sil veya sayıyı artır.
        # keys_to_scan = keys_to_scan[:50] 
        
        if keys_to_scan:
            print(f"{len(keys_to_scan)} dizi taranacak...")
            chunk_size = 10
            for i in range(0, len(keys_to_scan), chunk_size):
                chunk = keys_to_scan[i:i+chunk_size]
                batch_tasks = [process_series(session, db[k]) for k in chunk]
                
                scanned_series = await asyncio.gather(*batch_tasks)
                
                for s in scanned_series:
                    db[s["id"]] = s
                
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=2)

    # M3U Oluştur
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
