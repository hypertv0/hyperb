import json
import os
import sys
import time
import re
from tqdm import tqdm

# Selenium
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
    """Chrome Ayarları - Maksimum Uyumluluk"""
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    # Sayfa yüklenmesini bekleme stratejisi: 'eager' (HTML yüklensin yeter, resimleri bekleme)
    options.page_load_strategy = 'eager'
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def find_working_domain():
    """Çalışan domaini tespit et"""
    print("🤖 Domain tespiti yapılıyor (Chrome)...")
    for i in range(START_DOMAIN_NUM, END_DOMAIN_NUM):
        url = f"https://dizilla{i}.com"
        try:
            DRIVER.get(url)
            time.sleep(2)
            if "dizilla" in DRIVER.title.lower():
                print(f"✅ AKTİF DOMAIN BULUNDU: {url}")
                return url
        except:
            pass
    return None

def extract_episode_links(page_content, base_url):
    """
    Sayfa içeriğindeki (HTML/XML/Text) TÜM metinden linkleri ayıklar.
    Regex kullanmaz, kaba kuvvet ile string analizi yapar.
    """
    found_links = set()
    
    # 1. İçerikteki tüm "http" ile başlayan kelimeleri bul
    # Bu regex, boşluk veya tırnak görene kadar olan her şeyi url sanar.
    raw_urls = re.findall(r'https?://[^\s<>"\'()]+', page_content)
    
    base_clean = base_url.replace("https://", "").replace("http://", "")
    
    for url in raw_urls:
        # URL temizliği (XML tagleri yapışmış olabilir)
        url = url.split("<")[0].split(">")[0]
        
        # Sadece bizim domaini ve bölüm linklerini al
        # Kriter: "dizilla" geçecek, "-sezon-" geçecek, "-bolum" geçecek.
        if base_clean in url and "-sezon-" in url and "-bolum" in url:
            found_links.add(url)
            
    return list(found_links)

def parse_url_info(url):
    """URL'den Dizi Adı, Sezon ve Bölüm bilgisini çıkarır"""
    # Örnek: https://dizilla40.com/miss-fallaci-1-sezon-7-bolum
    try:
        # Son kısmı al: miss-fallaci-1-sezon-7-bolum
        slug_part = url.rstrip("/").split("/")[-1]
        
        # Regex ile parçala
        match = re.search(r'^(.*?)-(\d+)-sezon-(\d+)-bolum', slug_part)
        if match:
            name_slug = match.group(1)
            season = int(match.group(2))
            episode = int(match.group(3))
            
            title = name_slug.replace("-", " ").title()
            return title, season, episode, name_slug
    except:
        pass
    
    # Eğer regex tutmazsa manuel parse dene (Fallback)
    try:
        parts = url.split("-")
        # Son eleman 'bolum', ondan önceki '7', ondan önceki 'sezon', ondan önceki '1'
        if parts[-1] == "bolum" and parts[-3] == "sezon":
            episode = int(parts[-2])
            season = int(parts[-4])
            name_slug = "-".join(parts[:-4]).split("/")[-1]
            title = name_slug.replace("-", " ").title()
            return title, season, episode, name_slug
    except:
        pass

    return "Bilinmeyen Dizi", 1, 1, "unknown"

def main():
    global DRIVER
    DRIVER = setup_driver()
    
    try:
        # 1. Domain Bul
        base_url = find_working_domain()
        if not base_url:
            print("❌ Hata: Çalışan site bulunamadı.")
            with open(OUTPUT_M3U, "w") as f: f.write("#EXTM3U\n")
            return

        # 2. Sitemap Listesi (Manuel Garanti Liste)
        # Sitede 192 tane olduğunu biliyoruz, 200'e kadar tarayalım.
        sitemap_urls = [f"{base_url}/sitemaps/sitemap-{i}.xml" for i in range(1, 201)]
        
        print(f"🌍 {len(sitemap_urls)} adet site haritası taranacak...")
        
        all_episodes_data = [] # [{title, season, episode, url, slug}]
        
        # İlerleme çubuğu ile tarama
        pbar = tqdm(sitemap_urls, desc="Veri Çekiliyor")
        for sm_url in pbar:
            try:
                DRIVER.get(sm_url)
                
                # Sayfa kaynağını al (XML kodları dahil her şey)
                content = DRIVER.page_source
                
                # Eğer sayfa 404 ise veya boşsa geç
                if "404" in DRIVER.title or len(content) < 100:
                    continue
                
                # Linkleri ayıkla
                links = extract_episode_links(content, base_url)
                
                for link in links:
                    title, sea, ep, slug = parse_url_info(link)
                    all_episodes_data.append({
                        "title": title,
                        "season": sea,
                        "episode": ep,
                        "url": link,
                        "slug": slug
                    })
                
                # İlerleme çubuğuna bilgi ver
                pbar.set_postfix({"Bulunan": len(all_episodes_data)})
                
            except Exception:
                continue

        # 3. Tekilleştirme ve Sıralama
        # Aynı linkten birden fazla olabilir, temizleyelim
        unique_db = {}
        for item in all_episodes_data:
            unique_db[item['url']] = item
            
        final_list = list(unique_db.values())
        print(f"\n🔥 Toplam {len(final_list)} benzersiz bölüm bulundu!")
        
        # 4. Verileri Düzenle (Diziye göre grupla)
        # Poster ataması yapalım (Tahmini)
        series_groups = {}
        for item in final_list:
            slug = item['slug']
            if slug not in series_groups:
                # Poster URL tahmini (Macellan CDN)
                poster = f"https://file.macellan.online/images/tv/poster/f/f/100/{slug.replace('-','')}.jpg"
                series_groups[slug] = {
                    "title": item['title'],
                    "poster": poster,
                    "episodes": []
                }
            series_groups[slug]["episodes"].append(item)

        # 5. M3U Dosyasını Yaz
        print("💾 M3U Dosyası oluşturuluyor...")
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            
            # Dizileri alfabetik sırala
            for slug in sorted(series_groups.keys()):
                series = series_groups[slug]
                
                # Bölümleri sırala (Sezon -> Bölüm)
                episodes = sorted(series["episodes"], key=lambda x: (x['season'], x['episode']))
                
                for ep in episodes:
                    full_title = f"{ep['title']} - S{ep['season']} B{ep['episode']}"
                    
                    # M3U Entry
                    f.write(f'#EXTINF:-1 group-title="Dizilla" tvg-logo="{series["poster"]}", {full_title}\n')
                    f.write(f"{ep['url']}\n")
        
        # JSON Yedeği
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(series_groups, f, ensure_ascii=False, indent=2)

        print("✅ İŞLEM TAMAMLANDI! İyi seyirler.")

    except Exception as e:
        print(f"Kritik Hata: {e}")
    finally:
        if DRIVER:
            DRIVER.quit()

if __name__ == "__main__":
    main()
