import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import time
import sys

# --- AYARLAR ---
BASE_URL = "https://belgeselx.com"
OUTPUT_FILE = "playlist.m3u"
CONCURRENT_LIMIT = 5   # GitHub'da engellenmemek için hızı düşürdük
TIMEOUT = 30           # Bekleme süresini artırdık
MAX_RETRIES = 3

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

# Gerçek bir tarayıcı gibi görünmek için detaylı Header
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://belgeselx.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1"
}

FINAL_PLAYLIST = []

async def fetch_text(session, url, referer=None):
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer

    for i in range(MAX_RETRIES):
        try:
            async with session.get(url, headers=headers, timeout=TIMEOUT) as response:
                if response.status == 200:
                    return await response.text()
                elif response.status == 403:
                    print(f"!!! ERİŞİM ENGELLENDİ (403): {url}")
                    # 403 yedik, biraz bekleyip tekrar deneyelim
                    await asyncio.sleep(5)
                elif response.status == 404:
                    print(f"--- Sayfa Yok (404): {url}")
                    return None
                else:
                    print(f"--- Hata Kodu {response.status}: {url}")
        except Exception as e:
            print(f"--- Bağlantı Hatası: {e} | Url: {url}")
            await asyncio.sleep(2)
    return None

async def resolve_new4_php(session, episode_id, referer_url):
    api_url = f"{BASE_URL}/video/data/new4.php?id={episode_id}"
    text = await fetch_text(session, api_url, referer=referer_url)
    
    if not text:
        return None

    matches = re.findall(r'file:"([^"]+)", label: "([^"]+)"', text)
    if matches:
        best_link = matches[0][0]
        for link, label in matches:
            if "1080" in label or "FULL" in label:
                best_link = link
                break
        return best_link
    return None

async def process_content_page(session, semaphore, cat_name, title, url, poster):
    async with semaphore:
        html = await fetch_text(session, url)
        if not html: return

        soup = BeautifulSoup(html, 'html.parser')
        
        # Dizi Kontrolü
        episode_links = soup.select("div.gen-movie-contain div.gen-movie-info h3 a")
        
        if episode_links:
            # DİZİ
            tasks = []
            episode_infos = []
            
            for ep in episode_links:
                ep_href = ep['href']
                full_title = f"{title} - {ep.text.strip()}"
                episode_infos.append((full_title, ep_href))
                tasks.append(fetch_text(session, ep_href))
            
            ep_pages_html = await asyncio.gather(*tasks)
            video_tasks = []
            valid_episodes = [] 
            
            for i, ep_html in enumerate(ep_pages_html):
                if not ep_html: continue
                ep_soup = BeautifulSoup(ep_html, 'html.parser')
                watch_div = ep_soup.select_one(".fnc_addWatch")
                
                if watch_div and watch_div.has_attr("data-episode"):
                    eid = watch_div["data-episode"]
                    video_tasks.append(resolve_new4_php(session, eid, episode_infos[i][1]))
                    valid_episodes.append(i) 
            
            video_urls = await asyncio.gather(*video_tasks)
            
            for k, v_url in enumerate(video_urls):
                if v_url:
                    idx = valid_episodes[k]
                    t_title, _ = episode_infos[idx]
                    FINAL_PLAYLIST.append({
                        "group": cat_name, "title": t_title, "logo": poster, "url": v_url
                    })
                    print(f"  [+] Eklendi: {t_title}")

        else:
            # FİLM
            watch_div = soup.select_one(".fnc_addWatch")
            if watch_div and watch_div.has_attr("data-episode"):
                eid = watch_div["data-episode"]
                v_url = await resolve_new4_php(session, eid, url)
                if v_url:
                    FINAL_PLAYLIST.append({
                        "group": cat_name, "title": title, "logo": poster, "url": v_url
                    })
                    print(f"  [+] Eklendi: {title}")

async def scan_category(session, semaphore, cat_name, cat_url):
    print(f"Kategori Başladı: {cat_name}")
    page = 1
    tasks = []
    
    while True:
        # Test amaçlı şimdilik her kategoriden 2 sayfa çekelim
        # Eğer çalışırsa bu sayıyı artırırız veya kaldırırız
        if page > 2: break
        
        full_url = f"{cat_url}&page={page}"
        html = await fetch_text(session, full_url)
        
        if not html: break
        
        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select("div.gen-movie-contain")
        
        if not items: break
        
        for item in items:
            t_tag = item.select_one("div.gen-movie-info h3 a")
            if not t_tag: continue
            
            title = t_tag.text.strip()
            link = t_tag['href']
            img = item.select_one("div.gen-movie-img img")
            poster = img['src'] if img else ""
            
            tasks.append(process_content_page(session, semaphore, cat_name, title, link, poster))
            
        page += 1
    
    await asyncio.gather(*tasks)

async def main():
    print("Bot Başlatılıyor...")
    # SSL Doğrulamasını Kapat (GitHub IP Engelini Aşmak İçin Önemli)
    connector = aiohttp.TCPConnector(ssl=False)
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        cat_tasks = []
        for c_name, c_url in CATEGORIES.items():
            cat_tasks.append(scan_category(session, semaphore, c_name, c_url))
        
        await asyncio.gather(*cat_tasks)
        
    print(f"Toplam {len(FINAL_PLAYLIST)} içerik bulundu. Dosya yazılıyor...")
    
    FINAL_PLAYLIST.sort(key=lambda x: (x["group"], x["title"]))
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in FINAL_PLAYLIST:
            f.write(f'#EXTINF:-1 group-title="{item["group"]}" tvg-logo="{item["logo"]}", {item["title"]}\n')
            f.write(f'{item["url"]}\n')

if __name__ == "__main__":
    asyncio.run(main())
