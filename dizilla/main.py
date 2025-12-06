import json
import base64
import time
import re
import sys
import urllib.parse
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from bs4 import BeautifulSoup

# --- AYARLAR ---
REAL_BASE_URL = "https://dizilla40.com"
# Google Translate Proxy URL'si (Türkçe -> Türkçe çeviri yaparak siteyi olduğu gibi gösterir)
PROXY_BASE_URL = "https://dizilla40-com.translate.goog"
PROXY_PARAMS = "?_x_tr_sl=tr&_x_tr_tl=tr&_x_tr_hl=tr&_x_tr_pto=wapp"

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

def get_translate_url(original_url):
    """Normal URL'i Google Translate URL'ine çevirir"""
    if "translate.goog" in original_url:
        return original_url
    
    # https://dizilla40.com/dizi/x -> https://dizilla40-com.translate.goog/dizi/x?...
    path = original_url.replace(REAL_BASE_URL, "")
    return f"{PROXY_BASE_URL}{path}{PROXY_PARAMS}"

def extract_contentx(page, url):
    try:
        # ContentX URL'ini de Google Translate üzerinden açmayı dene
        # Ancak ContentX iframe olduğu için bazen direkt erişim gerekebilir.
        # Önce direkt deneyelim (ContentX genelde IP banlamaz), olmazsa Translate deneriz.
        
        tab = page.new_tab(url)
        time.sleep(4)
        
        html = tab.html
        match = re.search(r"window\.openPlayer\('([^']+)'", html)
        
        if match:
            extract_id = match.group(1)
            # Source2.php
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
        # Google Translate üzerinden git
        safe_url = get_translate_url(episode_url)
        log(f"Google üzerinden gidiliyor: {safe_url}", "DEBUG")
        
        page.get(safe_url)
        time.sleep(3)
        
        # Google Translate bazen iframe içine alır, bazen direkt gösterir.
        # __NEXT_DATA__ scriptini bulmaya çalışalım.
        
        script_text = None
        try:
            # Google Translate header'ını geçip içeriğe odaklan
            # Bazen script tagleri bozulabilir, text olarak arayalım
            html = page.html
            start_marker = '<script id="__NEXT_DATA__" type="application/json">'
            end_marker = '</script>'
            
            if start_marker in html:
                start_index = html.find(start_marker) + len(start_marker)
                end_index = html.find(end_marker, start_index)
                script_text = html[start_index:end_index]
        except:
            pass

        if not script_text:
            # Element olarak dene
            try:
                script_text = page.ele("#__NEXT_DATA__").text
            except:
                pass

        if not script_text:
            log("Sayfada __NEXT_DATA__ bulunamadı (Google Translate bozmuş olabilir).", "ERROR")
            return None
        
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
    # Google Translate çubuğunu gizlemek için
    co.set_argument('--disable-translate')
    
    page = ChromiumPage(co)
    
    try:
        log("DrissionPage (Google Proxy Modu) Başlatıldı.", "INFO")
        
        # Ana sayfaya Google Translate üzerinden git
        start_url = f"{PROXY_BASE_URL}/tum-bolumler{PROXY_PARAMS}"
        log(f"Bağlanılıyor: {start_url}", "INFO")
        
        page.get(start_url)
        
        # Cloudflare kontrolü (Google üzerinden gittiğimiz için çıkmamalı ama yine de bakalım)
        time.sleep(5)
        if "Access denied" in page.title or "Error 1005" in page.html:
            log("HATA: Google Translate üzerinden bile engellendi!", "CRITICAL")
            page.quit()
            return

        log("Ana sayfaya erişildi. İçerik taranıyor...", "SUCCESS")
        
        # Linkleri topla
        # Google Translate linkleri değiştirir, onları düzeltmemiz lazım
        # Linkler genelde şöyle olur: https://dizilla40-com.translate.goog/dizi/...?_x_tr...
        
        all_links = page.eles("tag:a")
        log(f"Sayfada {len(all_links)} link bulundu.", "INFO")
        
        items = []
        for link in all_links:
            try:
                href = link.attr("href")
                if not href: continue
                
                # Linkin dizi linki olup olmadığını kontrol et
                # Orijinal: /dizi/adi
                # Translate: https://dizilla40-com.translate.goog/dizi/adi?...
                
                if "/dizi/" not in href:
                    continue
                
                # Başlık ve Bölüm bilgisini al
                # Google Translate HTML yapısını biraz değiştirebilir, esnek olalım
                link_html = link.html
                soup = BeautifulSoup(link_html, 'html.parser')
                
                title_tag = soup.find("h2") or soup.find("h3") or soup.find("font") # Google bazen font tagi ekler
                if not title_tag: continue
                
                text_content = soup.get_text()
                if "Sezon" not in text_content and "Bölüm" not in text_content:
                    continue
                
                title = title_tag.text.strip()
                # Bölüm bilgisini metinden ayıkla (Regex ile)
                # Örnek: "Dizi Adı 2. Sezon 5. Bölüm"
                
                ep_match = re.search(r"(\d+)\.\s*Sezon\s*(\d+)\.\s*Bölüm", text_content)
                if ep_match:
                    ep_info = f"{ep_match.group(1)}x{ep_match.group(2)}"
                else:
                    # Fallback
                    ep_info = text_content.split("Sezon")[-1].strip()
                
                full_title = f"{title} - {ep_info}"
                
                # URL'i temizle (Google parametrelerini temizleyip orijinal domaini koyalım ki işlememiz kolay olsun)
                # Ama process_episode fonksiyonu zaten translate'e çevirecek.
                # Biz direkt translate linkini saklayalım ama temizleyelim.
                
                # Basitçe: Eğer link translate.goog içeriyorsa geçerlidir.
                
                # Tekrar kontrolü
                if not any(d['title'] == full_title for d in items):
                    items.append({"title": full_title, "url": href, "category": "Yeni"})
                    log(f"Bulundu: {full_title}", "DEBUG")
                
                if len(items) >= 20: break
                
            except Exception as e:
                continue

        if not items:
            log("İçerik bulunamadı. HTML yapısı değişmiş olabilir.", "WARNING")
            # log(page.html[:1000], "DEBUG")
        else:
            log(f"{len(items)} içerik işleniyor...", "INFO")
            
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                
                for item in items:
                    log(f"İşleniyor: {item['title']}", "INFO")
                    # URL zaten Google Translate URL'i olabilir, process_episode bunu halleder
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
