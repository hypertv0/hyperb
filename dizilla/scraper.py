import requests
from bs4 import BeautifulSoup
import os
import re
import time

# --- Configuration --- 
# IMPORTANT: YOU MUST UPDATE BASE_URL TO THE ACTUAL SITE YOU WANT TO SCRAPE.
# The CSS selectors used are generic and will likely need customization for your target site.
BASE_URL = "https://www.dizibox.tv" # Example placeholder, replace with actual site
OUTPUT_FILENAME = "dizilla.m3u"

# Resolve the output path to be in the same directory as the script
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), OUTPUT_FILENAME)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Connection": "keep-alive",
}

# --- Helper Functions ---
def fetch_page(url, retries=3, delay=2):
    print(f"Fetching: {url}")
    for i in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {url} (attempt {i+1}/{retries}): {e}")
            if i < retries - 1: # Don't delay after the last attempt
                time.sleep(delay) # Wait before retrying
    return None

def extract_full_url(base_url, relative_url):
    """Converts a relative URL to an absolute URL."""
    if not relative_url:
        return None
    if relative_url.startswith(('http://', 'https://')):
        return relative_url
    return requests.utils.urljoin(base_url, relative_url)

# --- Scraper Logic ---
def parse_homepage(html_content):
    """
    Parses the homepage to find links to series/movie detail pages.
    CRITICAL: YOU WILL LIKELY NEED TO CUSTOMIZE CSS SELECTORS HERE
    based on the actual HTML structure of your target site.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    items = []

    # Common CSS selectors for movie/series cards or links on a homepage.
    # Try various combinations:
    # 1. Links directly inside specific item containers (e.g., .post-item, .movie-card, .series-card)
    # 2. Links that have an h2/h3 title nearby

    # Attempt 1: Broad selection of common content card structures
    selectors = [
        'div.content-item a',
        'div.post a',
        'div.movie-card a',
        'div.series-card a',
        'article.post-item a',
        'article.movie-item a',
        'article.series-item a',
        'a[href*="/dizi/"]', # Specific to a 'dizi' (series) path
        'a[href*="/film/"]'  # Specific to a 'film' (movie) path
    ]

    for selector in selectors:
        for a_tag in soup.select(selector):
            href = a_tag.get('href')
            # Prioritize title from explicit tags, then alt/title attributes, then derive from URL
            title_tag = a_tag.find('h3') or a_tag.find('h2') or a_tag.find('p', class_=re.compile(r'title|name', re.IGNORECASE))
            title_text = title_tag.get_text(strip=True) if title_tag else None
            
            if not title_text:
                img_tag = a_tag.find('img')
                title_text = img_tag.get('alt') or img_tag.get('title') if img_tag else None

            if not title_text and href:
                # Fallback: try to get title from URL path
                path_segment = os.path.basename(href.strip('/')).replace('-', ' ').title()
                if path_segment and len(path_segment) > 3: # Avoid very short generic segments
                    title_text = path_segment

            if href and title_text and href != '#': # Filter out empty/invalid links
                full_url = extract_full_url(BASE_URL, href)
                if full_url and full_url not in [item['url'] for item in items]: # Avoid duplicates
                    items.append({"title": title_text, "url": full_url})

    print(f"Found {len(items)} unique items on homepage.")
    return items

def parse_detail_page(html_content, item_title, detail_page_url):
    """
    Parses a series/movie detail page to find episode links or indicates if it's a direct video source.
    CRITICAL: YOU WILL LIKELY NEED TO CUSTOMIZE CSS SELECTORS HERE.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    episodes = []

    # 1. Look for episode list (common for TV series)
    # Common selectors for episode lists:
    episode_selectors = [
        'ul.episodelist a',
        'div.episode-list a',
        '.season-episodes a',
        'div[class*="episode-box"] a',
        'a[href*="/bolum/"]' # Specific to a 'bolum' (episode) path
    ]

    for selector in episode_selectors:
        for episode_link_tag in soup.select(selector):
            href = episode_link_tag.get('href')
            title = episode_link_tag.get_text(strip=True) or f"Episode {len(episodes) + 1}"
            if href and title and href != '#':
                episodes.append({"title": f"{item_title} - {title}", "url": extract_full_url(BASE_URL, href)})
    
    # 2. If no episode list found, assume it might be a movie or a single-episode entry.
    # In this case, the detail page itself might contain the video player/link.
    if not episodes:
        print(f"No explicit episode list found for '{item_title}'. Assuming single video source from detail page.")
        # Create a dummy "episode" item for the main video, using the detail page URL as its source
        episodes.append({"title": item_title, "url": detail_page_url})

    return episodes

def extract_video_url(html_content, page_url):
    """
    Extracts the actual video URL (e.g., .m3u8, .mp4) from an episode/movie page.
    THIS IS THE MOST SITE-SPECIFIC PART AND WILL ALMOST CERTAINLY REQUIRE CUSTOMIZATION.
    It tries several common patterns:
    - Direct <video> or <source> tags.
    - Iframes pointing to external video players (attempts to fetch iframe content).
    - Regex for .m3u8 or .mp4 URLs directly in the HTML or script tags.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    video_url = None

    # Pattern 1: Direct <video> or <source> tags
    video_tag = soup.find('video')
    if video_tag and video_tag.get('src'):
        video_url = video_tag['src']
        print(f"Found video tag src: {video_url}")
    else:
        source_tag = soup.find('source', src=re.compile(r'\.(m3u8|mp4|webm|ogg)', re.IGNORECASE))
        if source_tag and source_tag.get('src'):
            video_url = source_tag['src']
            print(f"Found source tag src: {video_url}")

    # Pattern 2: Iframes pointing to video players
    if not video_url:
        # Common iframe patterns for players (e.g., embed.dizibox.tv, player.site.com)
        # Look for iframes whose src likely contains a video player.
        for iframe in soup.find_all('iframe', src=True):
            iframe_src = iframe['src']
            # Heuristic: if iframe src contains 'player', 'embed', 'video', 'stream', or direct video extensions
            if any(kw in iframe_src.lower() for kw in ['player', 'embed', 'video', 'stream', '.m3u8', '.mp4', '.webm', '.ogg']):
                print(f"Potentially relevant iframe src: {iframe_src}")
                iframe_full_url = extract_full_url(page_url, iframe_src)
                if iframe_full_url:
                    # Avoid re-fetching the main site itself if iframe is a relative path to main site
                    if not iframe_full_url.startswith(BASE_URL) or iframe_full_url == page_url:
                         # If iframe points to a different domain or a known player URL, try fetching it
                        print(f"Attempting to fetch content from iframe: {iframe_full_url}")
                        iframe_content = fetch_page(iframe_full_url)
                        if iframe_content:
                            # Recursively search for video URLs within iframe content
                            # This is a simplified recursion, not full deep dive.
                            found_in_iframe = re.search(r'(https?://[^"\\]+\.(?:m3u8|mp4|webm|ogg)(?:[^"\\]*)?)', iframe_content, re.IGNORECASE)
                            if found_in_iframe:
                                video_url = found_in_iframe.group(1)
                                print(f"Found video URL in iframe content via regex: {video_url}")
                                break
                            # Also look for <video> or <source> tags directly within the iframe content if it's full HTML
                            iframe_soup = BeautifulSoup(iframe_content, 'html.parser')
                            iframe_video_tag = iframe_soup.find('video')
                            if iframe_video_tag and iframe_video_tag.get('src'):
                                video_url = iframe_video_tag['src']
                                print(f"Found video tag src in iframe: {video_url}")
                                break
                            iframe_source_tag = iframe_soup.find('source', src=re.compile(r'\.(m3u8|mp4|webm|ogg)', re.IGNORECASE))
                            if iframe_source_tag and iframe_source_tag.get('src'):
                                video_url = iframe_source_tag['src']
                                print(f"Found source tag src in iframe: {video_url}")
                                break
            if video_url:
                break

    # Pattern 3: Regex for .m3u8 or .mp4 URLs directly in the HTML or script tags
    if not video_url:
        print("Searching for video URLs using regex in the entire HTML content (including script tags).")
        # This regex looks for http(s)://... followed by .m3u8 or .mp4, possibly with query params
        video_pattern = r'(https?://[^"\\]+\.(?:m3u8|mp4|webm|ogg)(?:[^"\\]*)?)'
        found_urls = re.findall(video_pattern, html_content, re.IGNORECASE)
        # Prioritize m3u8 if multiple formats found
        m3u8_urls = [url for url in found_urls if '.m3u8' in url]
        if m3u8_urls:
            video_url = m3u8_urls[0]
            print(f"Found m3u8 URL via regex: {video_url}")
        elif found_urls:
            video_url = found_urls[0]
            print(f"Found video URL via regex: {video_url}")
    
    if video_url:
        # Ensure the video URL is absolute before returning
        return extract_full_url(page_url, video_url)
    
    print(f"Could not find a video URL on page: {page_url}")
    return None

def main():
    m3u_entries = []
    
    homepage_html = fetch_page(BASE_URL)
    if not homepage_html:
        print("Failed to fetch homepage. Exiting.")
        return

    items_to_scrape = parse_homepage(homepage_html)
    
    if not items_to_scrape:
        print("No items found on homepage. Please check CSS selectors in parse_homepage and BASE_URL.")
        return

    for item in items_to_scrape:
        item_title = item['title']
        item_url = item['url']
        
        # Simple heuristic for categorization based on title or URL path
        group_title = "Series"
        if "film" in item_title.lower() or "movie" in item_title.lower() or ("film" in item_url.lower() and "dizi" not in item_url.lower()):
            group_title = "Movies"

        print(f"\nProcessing item: {item_title} ({item_url}) [Category: {group_title}]")
        
        detail_html = fetch_page(item_url)
        if not detail_html:
            continue

        episodes = parse_detail_page(detail_html, item_title, item_url)
        
        if not episodes:
            print(f"No episodes/video source identified for {item_title}. Skipping.")
            continue

        for episode in episodes:
            episode_title = episode['title']
            # If episode['url'] is None, it means the detail page itself contains the video (e.g., for a movie)
            page_to_extract_from_url = episode['url'] if episode['url'] else item_url
            
            if not page_to_extract_from_url:
                print(f"Invalid URL for episode: {episode_title}. Skipping.")
                continue

            page_to_extract_from_html = fetch_page(page_to_extract_from_url)
            
            if not page_to_extract_from_html:
                print(f"Failed to fetch content for '{episode_title}' from {page_to_extract_from_url}. Skipping.")
                continue

            video_link = extract_video_url(page_to_extract_from_html, page_to_extract_from_url)

            if video_link:
                m3u_entries.append(f'#EXTINF:-1 group-title="{group_title}",{episode_title}\n{video_link}')
            else:
                print(f"Could not find video link for '{episode_title}' from {page_to_extract_from_url}")

    # Write M3U file
    try:
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for entry in m3u_entries:
                f.write(entry + '\n')
        print(f"\nSuccessfully wrote M3U file to {OUTPUT_PATH} with {len(m3u_entries)} entries.")
    except IOError as e:
        print(f"Error writing M3U file: {e}")

if __name__ == "__main__":
    main()
