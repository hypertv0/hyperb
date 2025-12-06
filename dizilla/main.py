import json
import base64
import time
import re
import sys
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
BASE_URL = "https://dizilla40.com"
AES_KEY = b"9bYMCNQiWsXIYFWYAu7EkdsSbmGBTyUI"
AES_IV = bytes([0] * 16)
OUTPUT_FILE = "dizilla.m3u"

def log(message, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}")
    sys.stdout.flush()

def decrypt_dizilla_response(encrypted_data):
    try:
        if not encrypted_data: return None
        encrypted_bytes = base64.b64decode(encrypted_data)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except:
        return None

def extract_contentx(page, url):
    try:
        tab = page.new_tab(url)
        time.sleep(4) # Yüklenmesi için bekle
        
        html = tab.html
        match = re.search(r"window\.openPlayer\('([^']+)'", html)
        
        if match:
            extract_id = match.group(1)
            source_url = f"https://contentx.me/source2.php?v={extract_id}"
            tab.get(source_url)
            time.sleep(2)
            
            vid_html = tab.html
            vid_match = re.search(r'file":"([^"]+)"', vid_html)
            
            tab.close()
            if vid_match:
                return vid_match.group(1).replace("\\", "")
        else:
            tab.close()
    except Exception as e:
        log(f"ContentX hatası: {e}", "DEBUG")
        try: tab.close() 
        except: pass
    return None

def process_episode(page, episode_url):
    try:
        page.get(episode_url)
        time.sleep(2)
        
        # Cloudflare kontrolü
        if "Just a moment" in page.title:
            log("Bölüm sayfasında Cloudflare çıktı, bekleniyor...", "WARNING")
            time.sleep(5)

        # __NEXT_DATA__ scriptini çek
        try:
            script_text = page.ele("#__NEXT_DATA__", timeout=5).text
        except:
            log("Sayfada __NEXT_DATA__ bulunamadı.", "ERROR")
            return None

        if not script_text: return None
        
        data = json.loads(script_text)
        secure_data = data.get("props", {}).get("pageProps", {}).get("secureData", "")
        decrypted = decrypt_dizilla_response(secure_data)
        
        if not decrypted: return None
        
        page_data = json.loads(decrypted)
        sources = page_data.get("RelatedResults", {}).get("getEpisodeSources", {}).get("result", [])
        
        if not sources: return None
        
        iframe_html = sources[0].get("source_content", "")
        iframe_src = BeautifulSoup(iframe_html, 'html.parser').find("iframe")
        
        if not iframe_src: return None
        
        src = iframe_src.get("src")
        if src.startswith("//"): src = "https:" + src
        
        if "contentx.me" in src or "hotlinger" in src:
            return extract_contentx(page, src)
        
        return src
    except Exception as e:
        log(f"Bölüm işleme hatası: {e}", "ERROR")
        return None

def main():
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    # Headless KAPALI (Xvfb ile çalışacak)
    
    page = ChromiumPage(co)
    
    try:
        log("DrissionPage Başlatıldı.", "INFO")
        
        url = f"{BASE_URL}/tum-bolumler"
        page.get(url)
        
        # Cloudflare Bekleme
        for i in range(15):
            if "Just a moment" in page.title or "Access denied" in page.title:
                log(f"Cloudflare bekleniyor... ({i+1}/15)", "WARNING")
                time.sleep(2)
            else:
                break
        
        if "Just a moment" in page.title:
            log("HATA: Cloudflare aşılamadı.", "CRITICAL")
            page.quit()
            return

        log("Ana sayfaya erişildi. İçerik taranıyor...", "SUCCESS")
        time.sleep(5) # Sayfanın tamamen yüklenmesi (hydration) için bekle
        
        # --- GENEL TARAMA STRATEJİSİ ---
        # Belirli bir class aramak yerine, tüm linkleri alıp analiz ediyoruz.
        # Kotlin koduna göre yapı: <a> -> <h2>(Başlık) + <div>(Bölüm No)
        
        all_links = page.eles("tag:a")
        log(f"Sayfada toplam {len(all_links)} link bulundu. Analiz ediliyor...", "INFO")
        
        items = []
        for link in all_links:
            try:
                # Linkin içine bak
                link_html = link.html
                href = link.attr("href")
                
                if not href or "/dizi/" not in href:
                    continue
                
                # HTML parse et
                soup = BeautifulSoup(link_html, 'html.parser')
                
                # Başlık var mı? (h2 veya h3 olabilir)
                title_tag = soup.find("h2") or soup.find("h3")
                if not title_tag: continue
                
                # Bölüm bilgisi var mı? (Genelde opacity class'ı veya metin içinde Sezon/Bölüm geçer)
                # Kotlin kodu: div.opacity-80
                ep_tag = soup.select_one("div.opacity-80")
                
                # Eğer opacity div yoksa, metin içeriğine bak
                if not ep_tag:
                    text_content = soup.get_text()
                    if "Sezon" not in text_content and "Bölüm" not in text_content:
                        continue
                    ep_info = text_content # Fallback
                else:
                    ep_info = ep_tag.text.strip()

                title = title_tag.text.strip()
                
                # Temizleme
                ep_clean = ep_info.replace(". Sezon ", "x").replace(". Bölüm", "").strip()
                # Eğer ep_clean çok uzunsa (yanlış veri), başlığı kullanma
                if len(ep_clean) > 20: continue 

                full_title = f"{title} - {ep_clean}"
                full_url = f"{BASE_URL}{href}"
                
                # Tekrar eklemeyi önle
                if not any(d['url'] == full_url for d in items):
                    items.append({"title": full_title, "url": full_url, "category": "Yeni"})
                    log(f"Bulundu: {full_title}", "DEBUG")
                    
                if len(items) >= 20: break # 20 tane yeter
                
            except Exception as e:
                continue

        if len(items) == 0:
            log("HİÇ İÇERİK BULUNAMADI! Sayfa yapısı debug ediliyor...", "CRITICAL")
            # Sayfanın HTML'ini loglara bas (ilk 2000 karakter)
            html_dump = page.html
            log(f"HTML DUMP (İlk 2000 karakter):\n{html_dump[:2000]}", "DEBUG")
        else:
            log(f"{len(items)} adet geçerli bölüm bulundu. Linkler çözülüyor...", "INFO")
        
        # 3. M3U Oluştur
        if items:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in items:
                    log(f"İşleniyor: {item['title']}", "INFO")
                    stream_url = process_episode(page, item["url"])
                    
                    if stream_url:
                        ua = page.user_agent
                        final_url = f"{stream_url}|User-Agent={ua}" if ".m3u8" in stream_url else stream_url
                        
                        f.write(f'#EXTINF:-1 group-title="{item["category"]}", {item["title"]}\n')
                        f.write(f"{final_url}\n")
                        log("Eklendi.", "SUCCESS")
                    else:
                        log("Link bulunamadı.", "WARNING")
                    
                    time.sleep(1)

    except Exception as e:
        log(f"Kritik Hata: {e}", "CRITICAL")
    finally:
        page.quit()

if __name__ == "__main__":
    main()
