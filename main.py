import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import time
import sys

# --- AYARLAR ---
BASE_URL = "https://belgeselx.com"
OUTPUT_FILE = "playlist.m3u"
CONCURRENT_LIMIT = 20  # Hız için artırdık (Android'de sorun çıkarsa 10 yapın)
TIMEOUT = 15           # İstek zaman aşımı (saniye)
MAX_RETRIES = 3        # Hata durumunda tekrar deneme sayısı

# Kategoriler
CATEGORIES = {
    "Türk Tarihi": f"{BASE_URL}/konu/turk-tarihi-belgeselleri",
    "Tarih": f"{BASE_URL}/konu/tarih-belgeselleri",
    "Bilim": f"{BASE_URL}/konu/bilim-belgeselleri",
    "Seyahat": f"{BASE_URL}/konu/seyehat-belgeselleri",
    "Savaş": f"{BASE_URL}/konu/savas-belgeselleri",
    "Sanat": f"{BASE_URL}/konu/sanat-belgeselleri",
    "Polisiye": f"{BASE_URL}/konu/polisiye-belgeselleri",
    "Teknoloji": f"{BASE_URL}/konu/muhendislik-belgeselleri",
    "Doğa": f"{BASE_URL}/konu/doga-belgeselleri",
    "Hayvanlar": f"{BASE_URL}/konu/hayvan-belgeselleri",
    "Kozmik": f"{BASE_URL}/konu/kozmik-belgeseller"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Sonuçları toplayacağımız liste (Thread-safe gerekmez, asyncio tek thread çalışır)
FINAL_PLAYLIST = []

async def fetch_text(session, url, referer=None):
    """URL'ye istek atar, hata olursa tekrar dener."""
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer

    for i in range(MAX_RETRIES):
        try:
            async with session.get(url, headers=headers, timeout=TIMEOUT) as response:
                if response.status == 200:
                    return await response.text()
                elif response.status == 404:
                    return None
        except:
            await asyncio.sleep(1) # Hata olursa az bekle
    return None

async def resolve_new4_php(session, episode_id, referer_url):
    """Video ID'sini new4.php'ye gönderip gerçek linki (mp4/m3u8) alır."""
    api_url = f"{BASE_URL}/video/data/new4.php?id={episode_id}"
    text = await fetch_text(session, api_url, referer=referer_url)
    
    if not text:
        return None

    # Regex: file:"url", label: "quality"
    matches = re.findall(r'file:"([^"]+)", label: "([^"]+)"', text)
    if matches:
        # En iyi kaliteyi seç (1080p > FULL > Diğerleri)
        best_link = matches[0][0]
        for link, label in matches:
            if "1080" in label or "FULL" in label:
                best_link = link
                break
        return best_link
    return None

async def process_content_page(session, semaphore, cat_name, title, url, poster):
    """
    Bir belgesel sayfasına girer.
    - Eğer bölüm listesi varsa (Dizi) -> Tüm bölümleri çeker.
    - Bölüm listesi yoksa (Film) -> Tek videoyu çeker.
    """
    async with semaphore:
        html = await fetch_text(session, url)
        if not html:
            return

        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. KONTROL: DİZİ Mİ? (Kotlin: episodes = document.select("div.gen-movie-contain")...)
        # Sitede bölümler genellikle 'div.gen-movie-contain' içinde 'div.gen-movie-info h3 a' yapısında olur.
        # Ancak ana sayfada da bu yapı olabilir. Bu yüzden sadece alt kısımdaki bölümleri almalıyız.
        
        # Sayfanın altındaki bölüm listesini bulmaya çalışalım
        episode_links = soup.select("div.gen-movie-contain div.gen-movie-info h3 a")
        
        # Bazen "Benzer İçerikler" de bu sınıfları kullanıyor olabilir. 
        # Ancak BelgeselX yapısında genellikle bölüm listesi bu şekildedir.
        
        if episode_links:
            # --- DİZİ MANTIĞI ---
            # Dizi ise bölümlerin kendi sayfalarına gitmeye gerek var mı?
            # EVET, çünkü her bölümün ID'si kendi sayfasında yazar.
            
            tasks = []
            episode_infos = []
            
            for ep in episode_links:
                ep_name = ep.text.strip()
                ep_href = ep['href']
                full_title = f"{title} - {ep_name}"
                episode_infos.append((full_title, ep_href))
                
                # Her bölüm sayfası için fetch görevi (Video ID'yi bulmak için)
                tasks.append(fetch_text(session, ep_href))
            
            # Tüm bölüm sayfalarını paralel çek
            ep_pages_html = await asyncio.gather(*tasks)
            
            # Şimdi her bölüm sayfasından ID'yi alıp videoyu çözeceğiz
            video_tasks = []
            valid_episodes = [] # Hangi bölümün hangi videoya ait olduğunu tutmak için
            
            for i, ep_html in enumerate(ep_pages_html):
                if not ep_html: continue
                
                ep_soup = BeautifulSoup(ep_html, 'html.parser')
                watch_div = ep_soup.select_one(".fnc_addWatch")
                
                if watch_div and watch_div.has_attr("data-episode"):
                    eid = watch_div["data-episode"]
                    # Video linkini çözmek için görev ekle
                    video_tasks.append(resolve_new4_php(session, eid, episode_infos[i][1]))
                    valid_episodes.append(i) # Bu indexteki bölümü işliyoruz
            
            # Video linklerini paralel çöz
            video_urls = await asyncio.gather(*video_tasks)
            
            # Listeye Ekle
            for k, v_url in enumerate(video_urls):
                if v_url:
                    original_idx = valid_episodes[k]
                    ep_title, _ = episode_infos[original_idx]
                    FINAL_PLAYLIST.append({
                        "group": cat_name,
                        "title": ep_title,
                        "logo": poster,
                        "url": v_url
                    })
            
            if len(video_urls) > 0:
                print(f"  [Dizi] {title} ({len(video_urls)} Bölüm Eklendi)")

        else:
            # --- TEK FİLM MANTIĞI ---
            # Bölüm listesi yok, direkt fnc_addWatch ara
            watch_div = soup.select_one(".fnc_addWatch")
            if watch_div and watch_div.has_attr("data-episode"):
                eid = watch_div["data-episode"]
                v_url = await resolve_new4_php(session, eid, url)
                if v_url:
                    FINAL_PLAYLIST.append({
                        "group": cat_name,
                        "title": title,
                        "logo": poster,
                        "url": v_url
                    })
                    print(f"  [Film] {title} Eklendi")
            else:
                # Ne dizi ne film (veya giriş yapılması gerekiyor / link kırık)
                pass

async def scan_category_pages(session, semaphore, cat_name, cat_url):
    """Bir kategorideki tüm sayfaları gezer ve içerik linklerini bulur."""
    print(f"--- Kategori: {cat_name} Başladı ---")
    page = 1
    tasks = []
    
    while True:
        # Sonsuz döngü ama içerik bitince kıracağız
        # Test için sayfa sayısını sınırlamak isterseniz: if page > 5: break
        
        full_url = f"{cat_url}&page={page}"
        html = await fetch_text(session, full_url)
        
        if not html:
            break
            
        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select("div.gen-movie-contain")
        
        if not items:
            # İçerik bitti
            break
            
        print(f" >> {cat_name} Sayfa {page} tarandı. ({len(items)} içerik bulundu)")
        
        # Sayfadaki içerikleri işleme sırasına al
        for item in items:
            title_tag = item.select_one("div.gen-movie-info h3 a")
            if not title_tag: continue
            
            content_title = title_tag.text.strip()
            content_url = title_tag['href']
            
            img_tag = item.select_one("div.gen-movie-img img")
            poster = img_tag['src'] if img_tag else ""
            
            # Asenkron işlem kuyruğuna ekle
            tasks.append(process_content_page(session, semaphore, cat_name, content_title, content_url, poster))
            
        page += 1
        
        # Çok fazla sayfa varsa biraz nefes aldır (Opsiyonel)
        if page > 50: break 
    
    # Tüm içeriklerin detaylarına in ve videoları çek
    await asyncio.gather(*tasks)

async def main():
    start_time = time.time()
    
    # Semaphore aynı anda kaç detay sayfasına/videoya gidileceğini sınırlar
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    
    async with aiohttp.ClientSession() as session:
        # Kategorileri paralel taramak yerine, kategorileri birleştirip işleri havuzlayacağız
        # Ancak basitlik için kategori bazlı ilerleyelim, çünkü process_content_page zaten paralel çalışacak.
        
        cat_tasks = []
        for c_name, c_url in CATEGORIES.items():
            cat_tasks.append(scan_category_pages(session, semaphore, c_name, c_url))
        
        # Tüm kategorileri AYNI ANDA taramaya başla
        await asyncio.gather(*cat_tasks)
        
    # M3U Dosyasını Oluştur
    print(f"\n--- M3U Dosyası Oluşturuluyor: {OUTPUT_FILE} ---")
    
    # Listeyi Alfabetik Sırala (Kategori -> Başlık)
    FINAL_PLAYLIST.sort(key=lambda x: (x["group"], x["title"]))
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in FINAL_PLAYLIST:
            f.write(f'#EXTINF:-1 group-title="{item["group"]}" tvg-logo="{item["logo"]}", {item["title"]}\n')
            f.write(f'{item["url"]}\n')
            
    duration = time.time() - start_time
    print(f"Tamamlandı! Toplam {len(FINAL_PLAYLIST)} video eklendi.")
    print(f"Süre: {duration:.2f} saniye")

if __name__ == "__main__":
    try:
        # Windows için event loop policy (Gerekirse)
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Durduruldu.")
