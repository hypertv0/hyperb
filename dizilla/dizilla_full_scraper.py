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
MAX_CONCURRENT_REQUESTS = 50 # XML dosyaları küçüktür, sayıyı artırabiliriz
SEM = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Global Değişkenler
CURRENT_BASE_URL = ""
HEADERS = {}
COOKIES = {}

def find_working_domain():
    """
    Selenium ile çalışan domaini bulur ve cookie alır.
    """
    print("🤖 Domain tespiti başlatılıyor (Chrome)...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(20)

    found_url = None
    final_cookies = {}
    final_ua = ""

    for i in range(START_DOMAIN_NUM, END_DOMAIN_NUM):
        url = f"https://dizilla{i}.com"
        try:
            driver.get(url)
            time.sleep(3) # Cloudflare
            
            if "dizilla" in driver.title.lower():
                print(f"✅ AKTİF DOMAIN BULUNDU: {url}")
                found_url = url
                
                for c in driver.get_cookies():
                    final_cookies[c['name']] = c['value']
                final_ua = driver.execute_script("return navigator.userAgent;")
                break
        except:
            pass
            
    driver.quit()
    return found_url, final_cookies, final_ua

async def fetch_text(session, url):
    """Verilen URL'in içeriğini (HTML/XML) çeker"""
    async with SEM:
        try:
            headers = HEADERS.copy()
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.text()
        except:
            pass
    return None

async def parse_sitemap(session, sitemap_url):
    """
    Bir sitemap XML dosyasını indirir ve içindeki linkleri ayıklar.
    """
    xml_content = await fetch_text(session, sitemap_url)
    urls = []
    if not xml_content: return urls
    
    try:
        # lxml-xml parser çok hızlıdır
        soup = BeautifulSoup(xml_content, 'lxml-xml')
        locs = soup.find_all('loc')
        for loc in locs:
            urls.append(loc.text.strip())
    except:
        pass
    return urls

async def get_series_metadata(session, series_slug, series_url):
    """
    Dizinin ana sayfasına girip Poster ve Başlık bilgisini alır.
    (Sitemap'te bu bilgiler yoktur, o yüzden HTML'e bakmalıyız)
    """
    html = await fetch_text(session, series_url)
    if not html:
        return {"title": series_slug.replace("-", " ").title(), "poster": ""}

    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # Başlık ve Poster bulma (Site tasarımına göre değişebilir, genel yaklaşımlar)
        poster_img = soup.find("div", class_="poster").find("img") if soup.find("div", class_="poster") else None
        
        if not poster_img:
            # Alternatif
            poster_img = soup.select_one("img[src*='file.macellan']")
            
        poster_url = ""
        if poster_img:
            poster_url = poster_img.get("data-src") or poster_img.get("src")
            # Poster URL düzeltme
            if poster_url and not poster_url.startswith("http"):
                 if "macellan" in poster_url:
                     poster_url = f"https:{poster_url}" if poster_url.startswith("//") else poster_url
                 else:
                     poster_url = f"https://file.macellan.online/{poster_url.lstrip('/')}"
        
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True).replace("İzle", "").strip() if title_tag else series_slug
        
        return {"title": title, "poster": poster_url}
        
    except:
        return {"title": series_slug, "poster": ""}

async def main():
    global CURRENT_BASE_URL, HEADERS, COOKIES
    
    # 1. Domain Bul
    url, cookies, ua = find_working_domain()
    if not url:
        print("❌ HATA: Çalışan domain bulunamadı.")
        # Boş dosya oluştur
        with open(OUTPUT_M3U, 'w') as f: f.write("#EXTM3U\n")
        return
        
    CURRENT_BASE_URL = url.rstrip("/")
    HEADERS = {"User-Agent": ua}
    COOKIES = cookies
    
    # SSL yoksay
    connector = aiohttp.TCPConnector(ssl=False)
    
    async with aiohttp.ClientSession(connector=connector, cookies=COOKIES, headers=HEADERS) as session:
        
        # 2. Ana Sitemap'i Çek
        print("🗺️ Sitemap Index indiriliyor...")
        sitemap_index_url = f"{CURRENT_BASE_URL}/sitemaps/sitemap-index.xml"
        sitemap_files = await parse_sitemap(session, sitemap_index_url)
        
        if not sitemap_files:
            # Fallback: Bazen sitemap-index yoktur, manuel deneriz
            print("⚠️ Sitemap Index boş, manuel liste oluşturuluyor...")
            sitemap_files = [f"{CURRENT_BASE_URL}/sitemaps/sitemap-{i}.xml" for i in range(1, 193)]
            
        print(f"Toplam {len(sitemap_files)} alt harita bulundu.")
        
        # 3. Alt Haritaları Tara (TÜM URL'leri topla)
        print("🌍 Tüm linkler toplanıyor (Bu işlem hızlıdır)...")
        tasks = [parse_sitemap(session, sm) for sm in sitemap_files]
        
        all_urls = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Link Taraması"):
            res = await f
            if res: all_urls.extend(res)
            
        print(f"Toplam {len(all_urls)} adet link bulundu.")
        
        # 4. Linkleri Analiz Et ve Grupla
        # URL Yapısı:
        # Dizi Ana Sayfa: /dizi/lost
        # Bölüm Sayfası:  /dizi/lost/1-sezon-1-bolum
        
        series_db = {} # {slug: {metadata...}}
        episodes_map = {} # {slug: [episodes...]}
        
        # Regex ile URL parçala
        # Örnek: .../dizi/lost/1-sezon-1-bolum
        ep_pattern = re.compile(r'/dizi/([\w-]+)/(\d+)-sezon-(\d+)-bolum')
        series_pattern = re.compile(r'/dizi/([\w-]+)$')
        
        print("Linkler analiz ediliyor...")
        for link in all_urls:
            # Önce domaini güncel olanla değiştirelim (Eski sitemap'te eski domain olabilir)
            if "http" in link:
                path = link.split("/", 3)[-1]
                full_link = f"{CURRENT_BASE_URL}/{path}"
            else:
                full_link = f"{CURRENT_BASE_URL}{link}"
            
            # Bölüm Kontrolü
            ep_match = ep_pattern.search(full_link)
            if ep_match:
                slug, season, episode = ep_match.groups()
                if slug not in episodes_map: episodes_map[slug] = []
                
                episodes_map[slug].append({
                    "season": int(season),
                    "episode": int(episode),
                    "url": full_link
                })
                # Eğer diziyi henüz db'ye eklemediysek, iskeletini oluştur
                if slug not in series_db:
                    series_db[slug] = {"url": f"{CURRENT_BASE_URL}/dizi/{slug}", "fetched": False}
                continue

            # Dizi Ana Sayfa Kontrolü
            series_match = series_pattern.search(full_link)
            if series_match:
                slug = series_match.group(1)
                if slug not in series_db:
                    series_db[slug] = {"url": full_link, "fetched": False}
        
        print(f"Toplam {len(series_db)} dizi ve binlerce bölüm tespit edildi.")

        # 5. Dizi Metadata'sını Çek (Poster ve Başlık İçin)
        # Her bölüm için sayfaya gitmek yerine, sadece Dizi Ana Sayfasına gidip bilgiyi alacağız.
        # Bu çok daha hızlıdır.
        
        # DB yükle (varsa)
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    saved_db = json.load(f)
                    # Var olan verileri koru
                    for k, v in saved_db.items():
                        if k in series_db and v.get("poster"):
                            series_db[k]["title"] = v["title"]
                            series_db[k]["poster"] = v["poster"]
                            series_db[k]["fetched"] = True
            except: pass

        # Metadata'sı eksik olanları tara
        missing_meta = [k for k, v in series_db.items() if not v.get("fetched")]
        
        if missing_meta:
            print(f"{len(missing_meta)} yeni dizi için poster/başlık indiriliyor...")
            meta_tasks = []
            for slug in missing_meta:
                meta_tasks.append(get_series_metadata(session, slug, series_db[slug]["url"]))
                
            # Chunking (20'şerli gruplar halinde)
            chunk_size = 20
            results = []
            
            # Metadata işlemini tqdm ile gösterelim
            for i in range(0, len(missing_meta), chunk_size):
                chunk_slugs = missing_meta[i:i+chunk_size]
                chunk_tasks = [get_series_metadata(session, s, series_db[s]["url"]) for s in chunk_slugs]
                
                chunk_results = await asyncio.gather(*chunk_tasks)
                
                # Sonuçları işle
                for idx, meta in enumerate(chunk_results):
                    slug = chunk_slugs[idx]
                    series_db[slug].update(meta)
                    series_db[slug]["fetched"] = True
                
                print(f"Metadata: {i + len(chunk_results)} / {len(missing_meta)} işlendi...", end="\r")

            print("\nMetadata işlemi tamamlandı.")

        # 6. Verileri Birleştir ve M3U Yaz
        print("M3U dosyası oluşturuluyor...")
        
        # Cache'i güncelle
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(series_db, f, ensure_ascii=False, indent=2)
            
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            # Dizileri alfabetik sırala
            sorted_slugs = sorted(series_db.keys())
            
            for slug in sorted_slugs:
                if slug not in episodes_map: continue
                
                meta = series_db[slug]
                episodes = episodes_map[slug]
                
                # Bölümleri numaraya göre sırala (Sezon -> Bölüm)
                episodes.sort(key=lambda x: (x["season"], x["episode"]))
                
                for ep in episodes:
                    # M3U Formatı
                    full_title = f"{meta.get('title', slug)} - S{ep['season']} B{ep['episode']}"
                    poster = meta.get("poster", "")
                    # Kategori (Dizilla sitemapinde kategori yok, genel Dizi diyoruz)
                    category = "Dizilla Dizileri"
                    
                    f.write(f'#EXTINF:-1 group-title="{category}" tvg-logo="{poster}", {full_title}\n')
                    f.write(f"{ep['url']}\n")

    print(f"✅ İŞLEM TAMAMLANDI! {OUTPUT_M3U} oluşturuldu.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
