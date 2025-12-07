import requests
from bs4 import BeautifulSoup
import re
import os
import time
from urllib.parse import urljoin, urlparse

BASE_URL = "https://www.dizibox.tv/" # Base URL, ensure trailing slash for urljoin
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

OUTPUT_DIR = "dizilla"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "dizilla.m3u")

def fetch_page(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status() # Raise an exception for HTTP errors
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

def find_m3u8_links(html_content):
    """
    Finds .m3u8 links within HTML content using regex.
    Looks for various patterns: data-src, src, content, script variables.
    """
    if not html_content:
        return []
    
    # Regex to capture various forms of M3U8 URLs, including those in JS strings
    # It attempts to capture full URLs starting with http(s) and ending with .m3u8,
    # optionally followed by query parameters.
    # It also considers URLs within quotes (single/double)
    m3u8_pattern = r"(?:\"|')?(https?:\/\/[^\s\"']*\.m3u8(?:\?[^\s\"']*)?)(?:\"|')?"
    
    # Use re.DOTALL to match across multiple lines, useful for script tags
    found_links = re.findall(m3u8_pattern, html_content, re.IGNORECASE | re.DOTALL)
    
    # Filter out common false positives if necessary (e.g., non-streaming links)
    return list(set(found_links)) # Return unique links

def get_video_link(detail_url):
    print(f"  Scraping detail page: {detail_url}")
    detail_page_html = fetch_page(detail_url)
    if not detail_page_html:
        return None

    # First, try to find direct m3u8 links on the detail page
    m3u8_links = find_m3u8_links(detail_page_html)
    if m3u8_links:
        print(f"    Found direct M3U8 link on detail page: {m3u8_links[0]}")
        return m3u8_links[0]

    soup = BeautifulSoup(detail_page_html, 'html.parser')

    # Look for iframes that might contain players
    for iframe in soup.find_all('iframe', src=True):
        iframe_src_raw = iframe['src']
        iframe_src = urljoin(detail_url, iframe_src_raw) # Make URL absolute
        
        print(f"    Found potential player iframe: {iframe_src}")

        # Heuristic: Filter out common non-video iframes or non-HTTP sources
        # Also, avoid iframes from the same domain which might be internal navigation, unless it explicitly looks like a player.
        # This is a generic guess, might need refinement for a specific site.
        if not iframe_src.startswith("http"):
            continue
        
        parsed_iframe_src = urlparse(iframe_src)
        parsed_detail_url = urlparse(detail_url)

        # Simple check for common non-player domains or obvious non-player content
        if any(keyword in iframe_src for keyword in ["facebook.com", "twitter.com", "ads?", "comment", "google.com", "youtube.com/embed", "vimeo.com/video"]):
            # Specific youtube/vimeo embeds are often direct, if we want them, this condition should be more specific
            # For M3U8, these are unlikely to contain m3u8 directly.
            continue
            
        # Avoid recursive calls if iframe points to the same page or very similar path
        if parsed_iframe_src.netloc == parsed_detail_url.netloc and \
           (parsed_iframe_src.path == parsed_detail_url.path or parsed_iframe_src.path.rstrip('/') == parsed_detail_url.path.rstrip('/')):
           continue

        # Try to fetch content from the iframe src
        iframe_html = fetch_page(iframe_src)
        if iframe_html:
            iframe_m3u8_links = find_m3u8_links(iframe_html)
            if iframe_m3u8_links:
                print(f"    Found M3U8 link in iframe content: {iframe_m3u8_links[0]}")
                return iframe_m3u8_links[0]
        time.sleep(0.5) # Be gentle with requests after iframe check

    # If no iframe, or iframe didn't yield a link, try to find video tags directly
    video_tag = soup.find('video')
    if video_tag:
        source_tag = video_tag.find('source', src=True)
        if source_tag and ".m3u8" in source_tag['src']:
            source_src = urljoin(detail_url, source_tag['src'])
            print(f"    Found M3U8 in video tag source: {source_src}")
            return source_src
        
        # Check if video tag itself has a src (rare for M3U8 usually a source child)
        if video_tag.get('src') and ".m3u8" in video_tag['src']:
             source_src = urljoin(detail_url, video_tag['src'])
             print(f"    Found M3U8 in video tag src: {source_src}")
             return source_src

    return None

def scrape_category(category_url, group_title):
    print(f"Scraping category: {group_title} from {category_url}")
    items = []
    page_num = 1
    max_pages_to_scrape = 3 # Increased slightly, but still limited

    while page_num <= max_pages_to_scrape:
        current_page_url = urljoin(category_url, f"page/{page_num}/") if page_num > 1 else category_url
        print(f"  Fetching page: {current_page_url}")
        html_content = fetch_page(current_page_url)
        if not html_content:
            print(f"  Failed to fetch content for {current_page_url}. Stopping pagination.")
            break

        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Generic selectors for items. Prioritize more specific common ones first.
        # Common on Dizibox-like sites: div.col-xx-x, a.post-thumb, article, div.movie-item
        item_containers = soup.find_all(
            ['div', 'article'], 
            class_=re.compile(r'(col-\w+-\d+|movie-item|film-box|poster-item|item-card|post-item)', re.IGNORECASE)
        )
        
        if not item_containers:
            # Fallback: Find any <a> tags that look like they link to a content page
            # This is very broad and might pick up unwanted links, but as a last resort.
            content_area = soup.find('div', class_=re.compile(r'(content|main-content|movie-grid|series-grid|listing-items)', re.IGNORECASE))
            if content_area:
                potential_links = content_area.find_all('a', href=True)
                # Filter these links to look for something that resembles a detail page (e.g., /series-name/, /movie-name/)
                item_containers = [link for link in potential_links if re.search(r'/(series|movie|dizi|film)/[^/]+/?$', link['href'], re.IGNORECASE)]
            
            if not item_containers:
                print(f"  No content items found on {current_page_url} with generic selectors. Stopping pagination for this category.")
                break

        new_items_found = 0
        for container in item_containers:
            link_tag = container if container.name == 'a' else container.find('a', href=True)
            if not link_tag:
                continue

            item_link_raw = link_tag['href']
            item_link = urljoin(BASE_URL, item_link_raw) # Make URL absolute

            # Avoid scraping category pages again, or other non-content links
            if item_link.rstrip('/') == category_url.rstrip('/') or 'page/' in item_link:
                continue

            # Title extraction: prioritize h3, h2, or alt/title of img, or text of the link itself
            item_title = None
            title_tag = container.find(['h3', 'h2'])
            if title_tag:
                item_title = title_tag.get_text(strip=True)
            else:
                img_tag = container.find('img', alt=True)
                if img_tag:
                    item_title = img_tag['alt'].strip()
                elif link_tag:
                    item_title = link_tag.get_text(strip=True)
            
            if not item_title:
                continue

            # Basic title cleanup: remove common suffixes like year or 'full izle'
            item_title = re.sub(r'\(\d{4}\)|\s*Full\s*İzle|\s*izle$', '', item_title, flags=re.IGNORECASE).strip()
            
            # Avoid duplicates if a site lists the same item multiple times on a page
            if {'title': item_title, 'link': item_link} in items:
                continue

            if item_title and item_link:
                items.append({'title': item_title, 'link': item_link})
                new_items_found += 1
        
        if new_items_found == 0 and page_num > 1:
            print(f"  No new items found on page {page_num} of {group_title}. Stopping pagination.")
            break # No new items on subsequent pages, likely end of content or broken pagination
        
        page_num += 1
        time.sleep(1) # Be gentle between pages

    return items


def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    all_streams = []
    
    # Generic categories assumed for Dizibox type sites
    categories = {
        "Series": urljoin(BASE_URL, "diziler/"),
        "Movies": urljoin(BASE_URL, "filmler/"),
    }

    # Add a set to keep track of already added stream URLs to avoid duplicates
    processed_stream_urls = set()

    for group_title, category_url in categories.items():
        items_in_category = scrape_category(category_url, group_title)
        
        for item in items_in_category:
            video_link = get_video_link(item['link'])
            if video_link:
                if video_link not in processed_stream_urls:
                    all_streams.append({
                        'title': item['title'],
                        'group_title': group_title,
                        'url': video_link
                    })
                    processed_stream_urls.add(video_link)
                else:
                    print(f"  Skipping duplicate stream for {item['title']} with URL: {video_link}")
            else:
                print(f"  No M3U8 link found for {item['title']} ({item['link']})")
            time.sleep(1) # Be gentle between detail pages
            
    # Write M3U file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for stream in all_streams:
            f.write(f'#EXTINF:-1 tvg-id="{stream["title"]}" tvg-name="{stream["title"]}" group-title="{stream["group_title"]}",{stream["title"]}\n')
            f.write(f'{stream["url"]}\n')
    
    print(f"\nSuccessfully created M3U file: {OUTPUT_FILE}")
    print(f"Total unique streams found: {len(all_streams)}")

if __name__ == "__main__":
    main()
