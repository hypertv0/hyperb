import json
import os
import sys
import time
import re
from tqdm import tqdm

# Selenium Kütüphaneleri
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- AYARLAR ---
START_DOMAIN_NUM = 39
END_DOMAIN_NUM = 60
OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"

# --- GLOBAL ---
DRIVER = None

def setup_driver():
    """Optimize edilmiş Hızlı Chrome Ayarları"""
    options = Options()
    options.add_argument("--headless") # Ekransız mod
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # Resimleri ve gereksiz şeyleri yükleme (Hız için kritik)
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.cookies": 2,
        "profile.managed_default_content_settings.javascript": 1,
        "profile.managed_default_content_settings.plugins": 2,
        "profile.managed_default_content_settings.popups": 2,
        "profile.managed_default_content_settings.geolocation": 2,
        "profile.managed_default_content_settings.media_stream": 2,
    }
    options.add_experimental_option("prefs", prefs)
    
    # Bot olduğumuzu gizle
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

def find_working_domain():
    """Çalışan domaini bulur (39-60 arası)"""
    print("🤖 Domain tespiti yapılıyor (Chrome)...")
    
    for i in range(START_DOMAIN_NUM, END_DOMAIN_NUM):
        url = f"https://dizilla{i}.com"
        try:
            DRIVER.get(url)
            # Sayfa başlığını kontrol et
            if "dizilla" in DRIVER.title.lower():
                print(f"✅ AKTİF DOMAIN: {url}")
                return url
        except:
            pass
    return None

def get_links_from_source(source_text, base_url):
    """
    Sayfa kaynağındaki (XML/HTML fark etmez) tüm dizi linklerini Regex ile bulur.
    Format: base_url/dizi-adi-1-sezon-1-bolum
    """
    # Regex: base_url/ slug - sezon - bolum
    # Örnek: https://dizilla40.com/miss-fallaci-1-sezon-7-bolum
    
    clean_base = base_url.replace("https://", "").replace("http://", "")
    
    # Pattern: Link içinde "sezon" ve "bolum" kelimeleri geçmeli
    pattern = r'https?://' + re.escape(clean_base) + r'/([\w-]+)-(\d+)-sezon-(\d+)-bolum'
    
    links = []
    matches = re.findall(pattern, source_text)
    
    for match in matches:
        slug, season, episode = match
        full_url = f"{base_url}/{slug}-{season}-sezon-{episode}-bolum"
        links.append({
            "slug": slug,
            "season": int(season),
            "episode": int(episode),
            "url": full_url
        })
    return links

def main():
    global DRIVER
    DRIVER = setup_driver()
    
    try:
        # 1. Domain Bul
        base_url = find_working_domain()
        if not base_url:
            print("❌ Çalışan site bulunamadı! İnternet bağlantısını veya site durumunu kontrol edin.")
            # Boş dosya oluştur ki workflow hata vermesin
            with open(OUTPUT_M3U, "w") as f: f.write("#EXTM3U\n")
            with open(CACHE_FILE, "w") as f: f.write("{}")
            return

        # 2. Sitemap Listesini Oluştur
        # Manuel liste oluşturuyoruz çünkü sitemap index okumak bazen sorun yaratıyor.
        # Genelde sitemap-1'den sitemap-200'e kadar gider.
        # Sitede 192 tane olduğunu loglardan gördük.
        print("🗺️ Sitemap listesi hazırlanıyor...")
        sitemap_urls = [f"{base_url}/sitemaps/sitemap-{i}.xml" for i in range(1, 201)]

        # 3. Tüm Linkleri Topla
        all_episodes = []
        print(f"🌍 {len(sitemap_urls)} adet site haritası taranıyor...")
        
        for sm_url in tqdm(sitemap_urls, desc="Tarama"):
            try:
                DRIVER.get(sm_url)
                page_source = DRIVER.page_source
                
                # Eğer sayfa boşsa veya hata varsa atla
                if "404" in DRIVER.title:
                    continue
                
                # Regex ile linkleri sök
                extracted = get_links_from_source(page_source, base_url)
                if extracted:
                    all_episodes.extend(extracted)
                    
            except Exception as e:
                # Bir sitemap hatası tüm işlemi durdurmasın
                continue

        # Tekilleştirme (Aynı bölüm birden fazla sitemapte olabilir)
        # URL'ye göre benzersiz yap
        unique_episodes = {e['url']: e for e in all_episodes}.values()
        unique_episodes = list(unique_episodes)
        
        print(f"🔥 Toplam {len(unique_episodes)} bölüm linki bulundu!")
        
        # 4. Verileri Grupla
        series_map = {} # {slug: {title, poster, episodes: []}}
        
        for ep in unique_episodes:
            slug = ep['slug']
            if slug not in series_map:
                # Başlığı slug'dan üret (Miss-fallaci -> Miss Fallaci)
                title = slug.replace("-", " ").title()
                # Poster URL'sini tahmin et (Macellan CDN yapısı)
                # Tam doğru olmasa da logoda resim görünür
                poster = f"https://file.macellan.online/images/tv/poster/f/f/100/{slug.replace('-','')}.jpg"
                
                series_map[slug] = {
                    "title": title,
                    "poster": poster,
                    "episodes": []
                }
            
            series_map[slug]["episodes"].append(ep)

        # 5. M3U Oluştur ve Kaydet
        print("💾 M3U Dosyası yazılıyor...")
        
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            # Dizileri isme göre sırala
            sorted_slugs = sorted(series_map.keys())
            
            for slug in sorted_slugs:
                data = series_map[slug]
                # Bölümleri sırala: Sezon -> Bölüm
                data["episodes"].sort(key=lambda x: (x["season"], x["episode"]))
                
                for ep in data["episodes"]:
                    full_title = f"{data['title']} - S{ep['season']} B{ep['episode']}"
                    
                    # M3U Formatı
                    # #EXTINF:-1 group-title="Dizi Adı" tvg-logo="...", Dizi Adı - S1 B1
                    # Link
                    
                    f.write(f'#EXTINF:-1 group-title="{data["title"]}" tvg-logo="{data["poster"]}", {full_title}\n')
                    f.write(f"{ep['url']}\n")
        
        # JSON Veritabanını da güncelle (Yedek olarak)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(series_map, f, ensure_ascii=False, indent=2)

        print(f"✅ İŞLEM BAŞARIYLA TAMAMLANDI! {len(unique_episodes)} bölüm eklendi.")

    except Exception as e:
        print(f"Beklenmeyen genel hata: {e}")
        # Hata durumunda boş dosya oluştur
        if not os.path.exists(OUTPUT_M3U): open(OUTPUT_M3U, 'w').close()
    finally:
        if DRIVER:
            DRIVER.quit()

if __name__ == "__main__":
    main()
