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

# Selenium Kütüphaneleri
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- AYARLAR ---
LANDING_URL = "https://dizilla.club" # Başlangıç noktası
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 10 # Selenium ile cookie aldığımız için sayıyı artırabiliriz
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global değişkenler
CURRENT_BASE_URL = ""
HEADERS = {}
COOKIES = {}

def get_real_domain_and_cookies():
    """
    Selenium kullanarak gerçek tarayıcı açar,
    dizilla.club'a gider, yönlendirmeyi bekler ve
    güncel domain + çerezleri (cookies) çalar.
    """
    print("🤖 Selenium başlatılıyor (Chrome)...")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless") # Ekransız mod (GitHub Actions için şart)
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") # Bot olduğumuzu gizle
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        print(f"🌍 {LANDING_URL} adresine gidiliyor...")
        driver.get(LANDING_URL)
        
        # Yönlendirme için 10 saniye bekle
        # Bazen "Siteye Git" butonu olabilir, onu kontrol et
        time.sleep(5)
        
        try:
            # Eğer sayfada "dizilla" geçen bir link varsa ve buton gibiyse tıkla
            # (Bu kısım opsiyonel, otomatik yönleniyorsa gerek yok)
            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                href = link.get_attribute("href")
                if href and "dizilla" in href and "club" not in href:
                    print(f"🖱️ Buton bulundu, tıklanıyor: {href}")
                    driver.get(href)
                    break
        except:
            pass
            
        time.sleep(5) # İyice yüklenmesini bekle

        final_url = driver.current_url.rstrip("/")
        user_agent = driver.execute_script("return navigator.userAgent;")
        selenium_cookies = driver.get_cookies()
        
        # Cookie'leri aiohttp formatına çevir
        cookie_dict = {}
        for cookie in selenium_cookies:
            cookie_dict[cookie['name']] = cookie['value']
            
        print(f"✅ HEDEF BULUNDU: {final_url}")
        print(f"🍪 Cookie Sayısı: {len(cookie_dict)}")
        
        driver.quit()
        return final_url, cookie_dict, user_agent

    except Exception as e:
        print(f"❌ Selenium Hatası: {e}")
        driver.quit()
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
    except Exception:
        return None

async def fetch_url(session, url, method="GET", data=None, extra_headers=None):
    async with SEM:
        try:
            req_headers = HEADERS.copy()
            # API istekleri için Referer önemli
            req_headers["Referer"] = f"{CURRENT_BASE_URL}/arsiv"
            req_headers["Origin"] = CURRENT_BASE_URL
            req_headers["X-Requested-With"] = "XMLHttpRequest"
            
            if extra_headers: req_headers.update(extra_headers)
            
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
                    
                    # Poster URL düzeltme
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
    
    # 1. Selenium ile Giriş Yap ve Bilgileri Al
    real_url, cookies, ua = get_real_domain_and_cookies()
    
    if not real_url or "dizilla.club" in real_url:
        print("!!! UYARI: Selenium yönlendirmeyi yakalayamadı veya hala landing page'de.")
        # Fallback: Eğer selenium başarısız olursa manuel tahmini bir domain deneyelim
        # Ancak Selenium genelde başarılı olur.
        if real_url: CURRENT_BASE_URL = real_url
        else: 
            print("Kritik hata: URL alınamadı.")
            return
    else:
        CURRENT_BASE_URL = real_url

    HEADERS = {"User-Agent": ua}
    COOKIES = cookies
    
    # SSL hatasını yoksay
    connector = aiohttp.TCPConnector(ssl=False)
    
    db = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except: pass

    async with aiohttp.ClientSession(connector=connector, cookies=COOKIES, headers=HEADERS) as session:
        print(f"🚀 Hızlı Tarama Başlıyor: {CURRENT_BASE_URL}")
        
        # 2. Dizi Listesi (Hızlıca ilk 100 sayfayı tara)
        # 100 sayfa x 24 dizi = 2400 dizi. Arşiv için yeterli.
        tasks = [scrape_series_page(session, i) for i in range(1, 101)]
        
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Dizi Listesi"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} dizi bulundu.")
        
        new_items = 0
        for s in results:
            s["url"] = f"{CURRENT_BASE_URL}/{s['id']}"
            if s["id"] not in db:
                db[s["id"]] = s
                new_items += 1
            else:
                db[s["id"]]["url"] = s["url"]
                db[s["id"]]["title"] = s["title"]
        
        print(f"{new_items} yeni dizi eklendi.")

        # 3. Bölüm Detayları
        keys_to_scan = list(db.keys())
        # Not: Eğer çok fazla diziyi baştan taramak istemiyorsan
        # Sadece "episodes" listesi boş olanları filtreleyebilirsin:
        # keys_to_scan = [k for k, v in db.items() if not v.get("episodes")]
        # Ama tam güncelleme için hepsini tarayalım:
        
        if keys_to_scan:
            print(f"{len(keys_to_scan)} dizi taranacak...")
            chunk_size = 20
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
