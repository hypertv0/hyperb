import asyncio
import aiohttp
import json
import os
import sys
import time
import re
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- AYARLAR ---
START_DOMAIN_NUM = 38
END_DOMAIN_NUM = 60
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"
MAX_CONCURRENT_REQUESTS = 20
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global
CURRENT_BASE_URL = ""
HEADERS = {}
COOKIES = {}

def find_working_domain():
    """
    Selenium ile çalışan siteyi bulur ve çerezleri alır.
    """
    print("🤖 Domain tespiti başlatılıyor (Chrome)...")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(15)

    found_url = None
    final_cookies = {}
    final_ua = ""

    # 39'dan 60'a kadar dene
    for i in range(START_DOMAIN_NUM, END_DOMAIN_NUM):
        url = f"https://dizilla{i}.com"
        # print(f"Testing: {url}...", end="\r")
        try:
            driver.get(url)
            time.sleep(2) # Cloudflare geçişi için bekle
            
            # Başlık kontrolü
            if "dizilla" in driver.title.lower():
                print(f"✅ AKTİF DOMAIN BULUNDU: {url}")
                found_url = url
                
                # Cookie al
                for c in driver.get_cookies():
                    final_cookies[c['name']] = c['value']
                
                final_ua = driver.execute_script("return navigator.userAgent;")
                break
        except:
            pass
            
    driver.quit()
    return found_url, final_cookies, final_ua

async def fetch_html(session, url):
    """HTML sayfasını indirir"""
    async with SEM:
        try:
            headers = HEADERS.copy()
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    return await resp.text()
        except:
            pass
    return None

async def scrape_catalog_page(session, page_num):
    """
    /diziler/sayfa/X adresini tarar ve dizileri bulur.
    API yerine HTML parse eder.
    """
    # Dizilla'nın standart sayfalama yapısı: /diziler/sayfa/1
    url = f"{CURRENT_BASE_URL}/diziler/sayfa/{page_num}"
    html = await fetch_html(session, url)
    
    series_list = []
    if not html: return series_list

    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # Dizilla temasında genelde diziler 'div.poster' veya 'div.item' içindedir.
        # En garanti yöntem: href'i "/dizi/" ile başlayan linkleri bulmak.
        
        # Tüm linkleri tara
        anchors = soup.find_all('a', href=True)
        
        for a in anchors:
            href = a['href']
            
            # Sadece dizi linklerini yakala
            if "/dizi/" in href and "bolum" not in href:
                # Linki temizle
                if not href.startswith("http"):
                    full_url = f"{CURRENT_BASE_URL}{href}" if href.startswith("/") else f"{CURRENT_BASE_URL}/{href}"
                else:
                    full_url = href
                
                # Slug (ID) al
                slug = href.strip("/").split("/")[-1]
                
                # Başlık ve Poster bulmaya çalış
                title = ""
                poster = ""
                
                # Başlık genelde linkin içindeki img'nin alt tagi veya a'nın texti olur
                img = a.find('img')
                if img:
                    title = img.get('alt', slug)
                    poster = img.get('data-src') or img.get('src') or ""
                else:
                    title = a.get_text(strip=True) or slug

                # Poster URL düzeltme
                if poster and not poster.startswith("http"):
                     if "macellan" in poster or "dizilla" in poster:
                         poster = f"https:{poster}" if poster.startswith("//") else poster
                     else:
                         poster = f"https://file.macellan.online/{poster.lstrip('/')}"
                
                if title:
                    series_list.append({
                        "id": slug,
                        "title": title,
                        "url": full_url,
                        "poster": poster,
                        "category": "Dizi",
                        "episodes": []
                    })
                    
        # Duplicate'leri temizle (Sayfada aynı linkten 2 tane olabilir)
        unique_series = {v['id']: v for v in series_list}.values()
        return list(unique_series)

    except Exception:
        return []

async def parse_series_episodes(session, series_data):
    """
    Dizi detay sayfasına girer ve bölüm linklerini toplar.
    """
    url = series_data["url"]
    html = await fetch_html(session, url)
    if not html: return series_data
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        episodes = []
        
        # 1. Sezon Linklerini Bul (Sayfada Varsa)
        season_links = soup.select("a[href*='-sezon']")
        season_urls = [url] # Ana sayfayı da ekle
        
        for s in season_links:
            href = s['href']
            full = f"{CURRENT_BASE_URL}{href}" if not href.startswith("http") else href
            if full not in season_urls:
                season_urls.append(full)
        
        # Sezon sayfalarını gez (veya sadece ana sayfa ise onu)
        # Hız için: Genelde ana sayfada tüm bölümler veya sezon sekmeleri olur.
        # Dizilla'da sezon sayfaları ayrı yükleniyor olabilir.
        
        for s_url in season_urls:
            # Eğer ana sayfa değilse indir, ana sayfaysa zaten elimizde (html)
            if s_url != url:
                s_html = await fetch_html(session, s_url)
                if not s_html: continue
                s_soup = BeautifulSoup(s_html, 'lxml')
            else:
                s_soup = soup
            
            # Sezon Numarasını Tahmin Et
            season_match = re.search(r'(\d+)-sezon', s_url)
            season_num = season_match.group(1) if season_match else "1"
            
            # Bölüm linklerini bul
            # Genelde: <a href="/dizi/lost/1-sezon-1-bolum">
            ep_links = s_soup.find_all('a', href=True)
            for ep_a in ep_links:
                href = ep_a['href']
                if "bolum" in href and "sezon" in href:
                    full_ep_url = f"{CURRENT_BASE_URL}{href}" if not href.startswith("http") else href
                    text = ep_a.get_text(strip=True)
                    
                    # Başlık Temizleme
                    ep_name = text if text else "Bölüm"
                    
                    # Listeye Ekle
                    episodes.append({
                        "season": season_num,
                        "name": ep_name,
                        "url": full_ep_url
                    })

        # Bölümleri Tekilleştir
        unique_eps = {e['url']: e for e in episodes}.values()
        series_data["episodes"] = list(unique_eps)
        
    except:
        pass
        
    return series_data

async def main():
    global CURRENT_BASE_URL, HEADERS, COOKIES
    
    # 1. Domain Bul
    url, cookies, ua = find_working_domain()
    if not url:
        print("❌ HATA: Hiçbir çalışan Dizilla sitesi bulunamadı!")
        # Boş dosya oluştur
        with open(OUTPUT_M3U, 'w') as f: f.write("#EXTM3U\n")
        with open(CACHE_FILE, 'w') as f: f.write("{}")
        return
        
    CURRENT_BASE_URL = url.rstrip("/")
    HEADERS = {"User-Agent": ua}
    COOKIES = cookies
    
    print(f"🚀 Hedef Site: {CURRENT_BASE_URL}")
    print("API kullanılmayacak, doğrudan HTML taranıyor...")

    db = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f: db = json.load(f)
        except: pass

    # SSL Yok Say
    connector = aiohttp.TCPConnector(ssl=False)
    
    async with aiohttp.ClientSession(connector=connector, cookies=COOKIES, headers=HEADERS) as session:
        
        # 2. Katalog Tarama (Sayfa 1'den 150'ye kadar)
        print("Diziler taranıyor...")
        tasks = [scrape_catalog_page(session, i) for i in range(1, 151)]
        
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Sayfa Tarama"):
            res = await f
            if res: results.extend(res)
            
        print(f"Toplam {len(results)} dizi bulundu.")
        
        # DB'ye kaydet
        new_count = 0
        for s in results:
            if s["id"] not in db:
                db[s["id"]] = s
                new_count += 1
            else:
                # URL ve Poster güncelle
                db[s["id"]]["url"] = s["url"]
                db[s["id"]]["poster"] = s["poster"]
                
        print(f"{new_count} yeni dizi eklendi.")
        
        # 3. Bölümleri Tara
        keys = list(db.keys())
        # keys = keys[:50] # Test için limit (İstersen kaldır)
        
        if keys:
            print("Bölüm detayları taranıyor...")
            chunk_size = 20
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i+chunk_size]
                batch_tasks = [parse_series_episodes(session, db[k]) for k in chunk]
                
                completed = await asyncio.gather(*batch_tasks)
                for s in completed:
                    db[s["id"]] = s
                
                # Ara Kayıt
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False, indent=2)

    # 4. M3U Oluştur
    print(f"M3U dosyası yazılıyor... ({len(db)} dizi)")
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for k, v in db.items():
            eps = v.get("episodes", [])
            for ep in eps:
                title = f"{v['title']} - S{ep['season']} {ep['name']}"
                logo = v.get("poster", "")
                link = ep['url']
                
                f.write(f'#EXTINF:-1 group-title="Dizilla" tvg-logo="{logo}", {title}\n')
                f.write(f"{link}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
