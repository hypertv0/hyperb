import json
import os
import sys
import time
import re

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- AYARLAR ---
# Aramaya başlayacağı domain aralığı
START_DOMAIN = 39
END_DOMAIN = 60

# Kaç sayfa dizi taranacak? (Sitede yaklaşık 150-200 sayfa var)
# Test için önce 5 yapabilirsin, tamamı için 200 yap.
MAX_CATALOG_PAGES = 200 

OUTPUT_M3U = "dizilla_archive.m3u"
CACHE_FILE = "dizilla_db.json"

# --- GLOBAL ---
DRIVER = None
BASE_URL = ""

def setup_driver():
    """Chrome Ayarları"""
    options = Options()
    options.add_argument("--headless") # Ekransız mod
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    # Resimleri yükleme (Hızlandırır)
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

def find_active_domain():
    """Çalışan siteyi bulur"""
    print("🔍 Güncel domain aranıyor...")
    for i in range(START_DOMAIN, END_DOMAIN):
        url = f"https://dizilla{i}.com"
        try:
            DRIVER.get(url)
            time.sleep(2)
            if "dizilla" in DRIVER.title.lower():
                print(f"✅ AKTİF SİTE BULUNDU: {url}")
                return url
        except:
            print(f"❌ {url} ulaşılamadı.")
    return None

def get_series_from_catalog(page_num):
    """
    /diziler/sayfa/X adresine gider ve oradaki dizi linklerini toplar
    """
    catalog_url = f"{BASE_URL}/diziler/sayfa/{page_num}"
    print(f"\n📂 Katalog Taranıyor: Sayfa {page_num}...")
    
    try:
        DRIVER.get(catalog_url)
        time.sleep(2) # Sayfanın yüklenmesi için bekle
        
        # Dizi kartlarını bul (Link yapısı /dizi/ olanlar)
        # Genelde <a> etiketleri içindedir
        links = DRIVER.find_elements(By.CSS_SELECTOR, "a[href^='/dizi/']")
        
        found_series = []
        for link in links:
            href = link.get_attribute("href")
            # Bölüm linklerini değil, sadece dizi ana sayfalarını al
            # Bölüm linklerinde 'sezon' veya 'bolum' yazar, dizi ana sayfasında yazmaz
            if href and "sezon" not in href and "bolum" not in href:
                found_series.append(href)
        
        # Tekilleştir
        found_series = list(set(found_series))
        print(f"   ↳ Bu sayfada {len(found_series)} adet dizi bulundu.")
        return found_series
        
    except Exception as e:
        print(f"   ⚠️ Sayfa hatası: {e}")
        return []

def scrape_episodes_from_series(series_url):
    """
    Bir dizinin sayfasına girer, tüm sezon/bölüm linklerini bulur.
    """
    try:
        DRIVER.get(series_url)
        # Javascript yüklemeleri için bekle
        time.sleep(1.5)
        
        series_name = "Bilinmeyen Dizi"
        try:
            # Başlığı H1 veya Title'dan al
            h1 = DRIVER.find_element(By.TAG_NAME, "h1")
            series_name = h1.text.replace("İzle", "").strip()
        except:
            series_name = series_url.split("/")[-1].replace("-", " ").title()

        try:
            # Posteri bul
            img = DRIVER.find_element(By.CSS_SELECTOR, "div.poster img")
            poster = img.get_attribute("src") or img.get_attribute("data-src")
        except:
            poster = ""

        # Bölüm linklerini topla
        # Genelde sayfanın altında "1. Sezon 1. Bölüm" gibi linkler olur.
        # href içinde "sezon" ve "bolum" geçen tüm linkleri al.
        episode_elements = DRIVER.find_elements(By.CSS_SELECTOR, "a[href*='sezon'][href*='bolum']")
        
        episodes_found = []
        
        print(f"   📺 Dizi: {series_name} taranıyor...")
        
        for ep in episode_elements:
            url = ep.get_attribute("href")
            text = ep.text or ep.get_attribute("innerText")
            
            # Linkten sezon ve bölüm numarasını çıkar
            # Örn: .../miss-fallaci-1-sezon-7-bolum
            match = re.search(r'-(\d+)-sezon-(\d+)-bolum', url)
            if match:
                s_num = match.group(1)
                e_num = match.group(2)
                
                full_title = f"{series_name} - S{s_num} B{e_num}"
                
                # EKRANA YAZDIR (İstediğin Özellik)
                # print(f"      ✅ Link Bulundu: {full_title}")
                
                episodes_found.append({
                    "title": full_title,
                    "url": url,
                    "poster": poster,
                    "season": int(s_num),
                    "episode": int(e_num)
                })
        
        # Tekilleştir (Sayfada aynı linkten 2 tane olabilir)
        unique_eps = {e['url']: e for e in episodes_found}.values()
        
        count = len(unique_eps)
        if count > 0:
            print(f"      ✨ Toplam {count} bölüm eklendi.")
        else:
            print(f"      ⚠️ Hiç bölüm bulunamadı! (Yapı farklı olabilir)")
            
        return list(unique_eps)

    except Exception as e:
        print(f"      ❌ Dizi tarama hatası: {e}")
        return []

def main():
    global DRIVER, BASE_URL
    DRIVER = setup_driver()
    
    # 1. Siteyi Bul
    BASE_URL = find_active_domain()
    if not BASE_URL:
        print("SİTE BULUNAMADI. ÇIKIŞ YAPILIYOR.")
        DRIVER.quit()
        return

    # Dosyayı sıfırla/başlat
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

    total_episodes_global = 0

    # 2. Katalog Sayfalarını Gez
    for page in range(1, MAX_CATALOG_PAGES + 1):
        series_urls = get_series_from_catalog(page)
        
        if not series_urls:
            print("   Bu sayfada dizi yok veya sayfa sonuna gelindi.")
            # Eğer arka arkaya 3 sayfa boş gelirse durdurabilirsin ama şimdilik devam etsin
            if page > 10 and not series_urls: # Güvenlik önlemi
                print("   Boş sayfa tespit edildi, tarama bitiyor.")
                break
        
        # 3. Bulunan Dizilerin İçine Gir
        for s_url in series_urls:
            episodes = scrape_episodes_from_series(s_url)
            
            # M3U'ya Ekle (Her dizi bittiğinde dosyaya yazar, veri kaybı olmaz)
            if episodes:
                with open(OUTPUT_M3U, "a", encoding="utf-8") as f:
                    # Bölümleri sırala
                    episodes.sort(key=lambda x: (x['season'], x['episode']))
                    
                    for ep in episodes:
                        f.write(f'#EXTINF:-1 group-title="Dizilla" tvg-logo="{ep["poster"]}", {ep["title"]}\n')
                        f.write(f"{ep['url']}\n")
                
                total_episodes_global += len(episodes)

    print(f"\n🏁 İŞLEM BİTTİ! Toplam {total_episodes_global} bölüm M3U dosyasına eklendi.")
    DRIVER.quit()

if __name__ == "__main__":
    main()
