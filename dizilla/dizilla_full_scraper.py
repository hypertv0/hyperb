import json
import os
import sys
import time
import re
from bs4 import BeautifulSoup
from tqdm import tqdm

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

# --- GLOBAL ---
DRIVER = None

def setup_driver():
    """Hızlandırılmış Chrome Ayarları"""
    options = Options()
    options.add_argument("--headless") # Ekransız mod
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Resimleri yükleme (Hız için kritik)
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

def find_working_domain():
    """Çalışan domaini bulur"""
    print("🤖 Domain tespiti yapılıyor (Chrome)...")
    
    for i in range(START_DOMAIN_NUM, END_DOMAIN_NUM):
        url = f"https://dizilla{i}.com"
        try:
            DRIVER.get(url)
            # Cloudflare varsa biraz bekle
            time.sleep(2)
            
            if "dizilla" in DRIVER.title.lower():
                print(f"✅ AKTİF DOMAIN: {url}")
                return url
        except:
            pass
    return None

def get_page_source_via_selenium(url):
    """
    URL'yi Selenium ile açar ve kaynak kodunu döner.
    Cloudflare bunu engelleyemez.
    """
    try:
        DRIVER.get(url)
        # XML dosyaları bazen 'view-source:' gerektirmez, direkt render olur.
        # Sayfanın yüklendiğinden emin olalım.
        return DRIVER.page_source
    except Exception as e:
        print(f"Hata ({url}): {e}")
        return None

def main():
    global DRIVER
    DRIVER = setup_driver()
    
    try:
        # 1. Domain Bul
        base_url = find_working_domain()
        if not base_url:
            print("❌ Çalışan site bulunamadı!")
            return

        # 2. Sitemap Index'i Selenium ile Aç
        sitemap_index_url = f"{base_url}/sitemaps/sitemap-index.xml"
        print(f"🗺️ Sitemap Index okunuyor: {sitemap_index_url}")
        
        index_html = get_page_source_via_selenium(sitemap_index_url)
        
        # XML parse et
        soup = BeautifulSoup(index_html, 'lxml')
        # Tarayıcı XML'i bazen HTML gibi render eder, bazen text.
        # Hem 'loc' hem de text içindeki linkleri arayalım.
        
        sitemap_urls = []
        # Yöntem A: XML tagleri varsa
        locs = soup.find_all("loc")
        for loc in locs:
            sitemap_urls.append(loc.text.strip())
            
        # Yöntem B: Eğer tagler yoksa ve text ise (Fallback)
        if not sitemap_urls:
            text = soup.get_text()
            sitemap_urls = re.findall(r'https://.*?sitemap-\d+\.xml', text)
            
        # Manuel Fallback (Eğer sitemap index boş görünürse)
        if not sitemap_urls:
            print("⚠️ Index okunamadı, manuel liste oluşturuluyor...")
            sitemap_urls = [f"{base_url}/sitemaps/sitemap-{i}.xml" for i in range(1, 150)]

        print(f"📄 Toplam {len(sitemap_urls)} alt sitemap bulundu.")

        # 3. Alt Sitemapleri Gez ve Linkleri Topla
        all_links = []
        print("🌍 Linkler toplanıyor (Selenium ile)...")
        
        # Her bir sitemap dosyasını Selenium ile ziyaret et
        for sm_url in tqdm(sitemap_urls, desc="Sitemap Okuma"):
            # Domain değişmiş olabilir, sitemap linkini güncelle
            if base_url not in sm_url:
                part = sm_url.split("/sitemaps/")[-1]
                sm_url = f"{base_url}/sitemaps/{part}"
                
            html = get_page_source_via_selenium(sm_url)
            if not html: continue
            
            sub_soup = BeautifulSoup(html, 'lxml')
            
            # Linkleri bul
            found = 0
            # <loc> tagleri
            for loc in sub_soup.find_all("loc"):
                link = loc.text.strip()
                all_links.append(link)
                found += 1
            
            # Eğer tag bulamazsa text regex (Chrome bazen XML'i text gösterir)
            if found == 0:
                text_content = sub_soup.get_text()
                # Link yapısı: https://dizilla40.com/dizi/...
                matches = re.findall(rf'{base_url}/dizi/[\w-]+(?:/[\w-]+)?', text_content)
                all_links.extend(matches)

        # Tekilleştir
        all_links = list(set(all_links))
        print(f"🔥 Toplam {len(all_links)} benzersiz link bulundu!")
        
        # 4. Veriyi İşle ve M3U Oluştur
        series_map = {}   # {slug: {title, poster, url}}
        episodes_list = [] # [{slug, season, episode, url}]
        
        print("Linkler analiz ediliyor...")
        
        # Regexler
        # /dizi/lost
        # /dizi/lost/1-sezon-1-bolum
        reg_ep = re.compile(r'/dizi/([\w-]+)/(\d+)-sezon-(\d+)-bolum')
        reg_series = re.compile(r'/dizi/([\w-]+)$')
        
        for link in all_links:
            # URL düzelt
            if not link.startswith("http"):
                link = f"{base_url}{link}"
            # Domain fix
            if base_url not in link:
                path = link.split("/", 3)[-1]
                link = f"{base_url}/{path}"
                
            # Bölüm mü?
            ep_match = reg_ep.search(link)
            if ep_match:
                slug, sea, ep = ep_match.groups()
                episodes_list.append({
                    "slug": slug,
                    "season": int(sea),
                    "episode": int(ep),
                    "url": link
                })
                # Diziyi haritaya ekle (henüz yoksa)
                if slug not in series_map:
                    title = slug.replace("-", " ").title()
                    # Varsayılan poster (Macellan sunucusu tahmini)
                    poster = f"https://file.macellan.online/images/{slug.replace('-','')}1.jpg"
                    series_map[slug] = {"title": title, "poster": poster}
                continue
                
            # Dizi Ana Sayfası mı?
            ser_match = reg_series.search(link)
            if ser_match:
                slug = ser_match.group(1)
                title = slug.replace("-", " ").title()
                # Burada gerçek posteri almak için sayfaya girmek gerekir
                # Ama hız için şimdilik varsayılan bırakıyoruz.
                # İstersen buraya bir Selenium 'get' ekleyebiliriz ama 3000 dizi uzun sürer.
                if slug not in series_map:
                    poster = f"https://file.macellan.online/images/{slug.replace('-','')}1.jpg"
                    series_map[slug] = {"title": title, "poster": poster}

        print(f"Tespit edilen: {len(series_map)} Dizi, {len(episodes_list)} Bölüm.")

        # 5. M3U Yaz
        print("💾 M3U Dosyası kaydediliyor...")
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            # Bölümleri sırala
            episodes_list.sort(key=lambda x: (x["slug"], x["season"], x["episode"]))
            
            for ep in episodes_list:
                slug = ep["slug"]
                info = series_map.get(slug, {"title": slug, "poster": ""})
                
                full_title = f"{info['title']} - S{ep['season']} B{ep['episode']}"
                poster = info['poster']
                
                f.write(f'#EXTINF:-1 group-title="Dizilla" tvg-logo="{poster}", {full_title}\n')
                f.write(f"{ep['url']}\n")

        print("✅ İŞLEM BAŞARIYLA TAMAMLANDI!")

    except Exception as e:
        print(f"Beklenmeyen hata: {e}")
    finally:
        if DRIVER:
            DRIVER.quit()

if __name__ == "__main__":
    main()
