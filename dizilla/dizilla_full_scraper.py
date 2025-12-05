import asyncio
import aiohttp
import json
import base64
import re
import os
import sys
import time
from Crypto.Cipher import AES
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- AYARLAR ---
# Tarayacağımız aralık. Şu an 40'larda olduğu için 39-55 arası güvenli.
START_RANGE = 39
END_RANGE = 55

AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 20 # Siteyi bulunca saldırı hızını artırabiliriz
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global değişkenler
CURRENT_BASE_URL = ""
HEADERS = {}
COOKIES = {}

def find_active_domain_with_selenium():
    """
    Landing page ile uğraşmadan, doğrudan olası domainleri dener.
    Hangisi açılırsa onu ve çerezlerini alır.
    """
    print("🤖 Selenium (Chrome) başlatılıyor...")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    driver.set_page_load_timeout(15)

    found_url = None
    final_cookies = {}
    final_ua = ""

    # Aralıkta dönerek deneme yap
    for i in range(START_RANGE, END_RANGE):
        candidate_url = f"https://dizilla{i}.com"
        print(f"📡 Deneniyor: {candidate_url} ... ", end="")
        
        try:
            driver.get(candidate_url)
            
            # Başlığı kontrol et. Eğer site açıksa başlıkta "Dizilla" yazar.
            # Cloudflare engeli varsa title genelde "Just a moment..." olur ama
            # Selenium beklediği için site açılınca başlık düzelir.
            time.sleep(3) # Cloudflare geçişi için kısa bekleme
            
            title = driver.title.lower()
            current_url = driver.current_url
            
            if "dizilla" in title and "club" not in current_url:
                print("✅ BAŞARILI!")
                found_url = current_url.rstrip("/")
                
                # Cookie'leri al
                selenium_cookies = driver.get_cookies()
                for cookie in selenium_cookies:
                    final_cookies[cookie['name']] = cookie['value']
                
                final_ua = driver.execute_script("return navigator.userAgent;")
                break
            else:
                print("❌ (Aktif değil veya başka sayfa)")
        
        except Exception as e:
            print(f"❌ (Erişim hatası)")

    driver.quit()
    return found_url, final_cookies, final_ua

def decrypt_dizilla_response(encrypted_str):
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

async def fetch_url(session, url, method="GET", data=None, extra_headers=None):
    async with SEM:
        try:
            req_headers = HEADERS.copy()
            req_headers["Referer"] = f"{CURRENT_BASE_URL}/arsiv"
            req_headers["Origin"] = CURRENT_BASE_URL
            req_headers["X-Requested-With"] = "XMLHttpRequest"
            
            if extra_headers: req_headers.update(extra_headers)
            
            async with session.request(method, url, data=data, headers=req_headers, timeout=20) as response:
                if response.status == 200:
                    try:
                        return await response.json()
                    except:
                        return await response.text()
                return None
        except Exception:
            return None

async def scrape_series_page(session, page_num):
    # API İsteği
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
                
                # URL Normalizasyonu
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
    global CURRENT_BASE_URL, HEADERS, COOKIES
    
    # 1. DOĞRUDAN DOMAIN BULMA
    found_url, cookies, ua = find_active_domain_with_selenium()
    
    if not found_url:
        print("!!! KRİTİK: Hiçbir Dizilla adresi (40-55 arası) aktif görünmüyor.")
        # Boş dosya oluşturup çık
        if not os.path.exists(OUTPUT_M3U): open(OUTPUT_M3U, 'w').close()
        if not os.path.exists(CACHE_FILE): open(CACHE_FILE, 'w').write("{}")
        return

    CURRENT_BASE_URL = found_url
    HEADERS = {"User-Agent": ua}
    COOKIES = cookies
    
    # SSL hatası bypass
    connector = aiohttp.TCPConnector(ssl=False)
    
    db = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except: pass

    async with aiohttp.ClientSession(connector=connector, cookies=COOKIES, headers=HEADERS) as session:
        print(f"🚀 Hızlı Tarama Başlıyor: {CURRENT_BASE_URL}")
        
        # 2. Dizi Listesi - 300 sayfa (Tüm arşivi garantiye alalım)
        # aiohttp ile bu işlem sadece 1-2 dakika sürer.
        tasks = [scrape_series_page(session, i) for i in range(1, 301)]
        
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Dizi Listesi"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} dizi bulundu.")
        
        # DB Güncelleme
        for s in results:
            s["url"] = f"{CURRENT_BASE_URL}/{s['id']}"
            if s["id"] not in db:
                db[s["id"]] = s
            else:
                db[s["id"]]["url"] = s["url"]
                db[s["id"]]["title"] = s["title"]

        # 3. Bölüm Detayları
        # Sadece bölümleri eksik olanları veya hepsini tarayalım
        # İlk seferde "hepsi" dememiz lazım.
        keys_to_scan = list(db.keys())
        
        # Eğer çok fazla dizi varsa ve timeout alıyorsan burayı açabilirsin:
        # keys_to_scan = keys_to_scan[:500] 
        
        if keys_to_scan:
            print(f"{len(keys_to_scan)} dizi için bölümler kontrol ediliyor...")
            chunk_size = 50 # Aynı anda 50 dizi taranacak
            
            for i in range(0, len(keys_to_scan), chunk_size):
                chunk = keys_to_scan[i:i+chunk_size]
                batch_tasks = [process_series(session, db[k]) for k in chunk]
                
                scanned_series = await asyncio.gather(*batch_tasks)
                for s in scanned_series:
                    db[s["id"]] = s
                
                # Her 50 dizide bir kaydet
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=2)

    # 4. M3U Oluştur
    print(f"M3U oluşturuluyor... ({len(db)} kayıt)")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s_id, data in db.items():
            episodes = data.get("episodes", [])
            # Bölümleri isme göre sıralayalım
            # (Basitçe dizi içindeki sıraya güveniyoruz, genelde doğrudur)
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
