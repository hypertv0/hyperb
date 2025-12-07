import os
import re
import cloudscraper
from bs4 import BeautifulSoup

# --- Configuration ---
BASE_URL = "https://www.dizibox.tv"  # IMPORTANT: This URL is a placeholder.
                                     # Adjust it to the actual site you want to scrape.
OUTPUT_FILENAME = "dizilla.m3u"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), OUTPUT_FILENAME)

# Headers to mimic a browser, good practice even with cloudscraper
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Initialize cloudscraper
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def get_soup(url: str) -> BeautifulSoup | None:
    """Fetches a URL and returns a BeautifulSoup object."""
    try:
        print(f"Fetching: {url}")
        response = scraper.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return BeautifulSoup(response.text, 'lxml')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_video_link(episode_url: str) -> str | None:
    """
    Extracts the M3U8 video link from an episode page.
    This is the most site-specific part and might require adjustment.
    """
    soup = get_soup(episode_url)
    if not soup:
        return None

    # Strategy 1: Look for M3U8 links directly within <script> tags
    for script in soup.find_all('script'):
        if script.string:
            # Regex to find M3U8 links. It tries to capture a full URL ending with .m3u8
            match = re.search(r"['\"](https?://[^\"']*\.m3u8[^\"']*)['\"]", script.string)
            if match:
                m3u8_url = match.group(1).strip()
                # Basic validation for common M3U8 patterns, though not exhaustive
                if m3u8_url.endswith('.m3u8') and m3u8_url.startswith('http'):
                    print(f"  -> Found M3U8 in script: {m3u8_url}")
                    return m3u8_url

    # Strategy 2 (Fallback): Look for iframes that might embed a player.
    # For a truly generic script, we only check if the iframe's src is directly an M3U8.
    # More complex scenarios (parsing the iframe's content) are site-specific.
    for iframe in soup.find_all('iframe'):
        iframe_src = iframe.get('src')
        if iframe_src and iframe_src.endswith('.m3u8') and iframe_src.startswith('http'):
            print(f"  -> Found M3U8 in iframe src: {iframe_src}")
            return iframe_src

    print(f"  -> No M3U8 link found on {episode_url}")
    return None


def main():
    m3u_entries = []
    print(f"Starting Dizilla scraper for {BASE_URL}...")

    # --- Phase 1: Scrape main page for series/movie links ---
    print("Scraping main page for series/movie links...")
    main_soup = get_soup(BASE_URL)
    if not main_soup:
        print("Failed to get main page. Exiting.")
        return

    content_links = set()
    
    # Generic selectors for items on the main page that link to series/movies
    # These are highly speculative and will likely need adjustment for a specific site.
    # We're looking for 'a' tags that have 'href' attributes pointing to what looks like content.
    potential_item_links = main_soup.find_all('a', href=True)
    
    for link_tag in potential_item_links:
        href = link_tag['href']
        # Normalize URL to be absolute
        if not href.startswith('http'):
            href = f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        
        # Filter for likely series/movie detail pages based on common URL patterns
        if ("/series/" in href or "/film/" in href or "/movie/" in href or "/dizi/" in href) and \
           ("/episode-" not in href and "/bolum-" not in href): # Avoid direct episode links here
            # Basic check for unique and relevant links
            if BASE_URL in href and '#' not in href and '?' not in href:
                content_links.add(href)
    
    # Limit the number of series/movies to process for a generic example
    # This prevents potentially huge scrapes on unknown sites and saves time.
    content_links = list(content_links)[:5] # Process top 5 unique links found

    if not content_links:
        print("No series/movie links found on the main page. Check selectors and BASE_URL.")
        return

    print(f"Found {len(content_links)} potential series/movie pages.")

    # --- Phase 2: Iterate through series/movie pages for episodes ---
    for item_url in sorted(list(content_links)): # Sort for consistent processing order
        category = "Series" if "/series/" in item_url or "/dizi/" in item_url else "Movies"
        
        # Attempt to get item title from the URL
        item_title_slug = item_url.rstrip('/').split('/')[-1]
        item_title = item_title_slug.replace('-', ' ').title() # Basic title formatting

        print(f"\nProcessing {category}: {item_title} ({item_url})")
        item_soup = get_soup(item_url)
        if not item_soup:
            print(f"  Skipping {item_title}: Failed to fetch page.")
            continue
        
        episode_links = set()
        
        # Generic selectors for episode links on a series/movie detail page
        # Assuming episode links contain "/episode-" or "/bolum-"
        potential_episode_tags = item_soup.find_all('a', href=True)
        for link_tag in potential_episode_tags:
            href = link_tag['href']
            if not href.startswith('http'):
                href = f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
            
            if BASE_URL in href and ("/episode-" in href or "/bolum-" in href):
                # Ensure the episode link belongs to the current item by checking the path segment
                item_path_segment = item_url.split(BASE_URL)[-1].strip('/')
                episode_path_segment = href.split(BASE_URL)[-1].strip('/')
                if item_path_segment and episode_path_segment.startswith(item_path_segment):
                    episode_links.add(href)
        
        # Limit episodes per series for generic example
        episode_links = list(episode_links)[:10] # Process top 10 unique episodes per item

        if not episode_links:
            print(f"  No episode links found for {item_title}. Check selectors.")
            continue

        print(f"  Found {len(episode_links)} potential episodes.")

        # --- Phase 3: Extract video links from episode pages ---
        for episode_url in sorted(list(episode_links)): # Sort for consistent order
            episode_title_slug = episode_url.rstrip('/').split('/')[-1]
            episode_title_suffix = episode_title_slug.replace('-', ' ').title()
            full_title = f"{item_title} - {episode_title_suffix}"
            
            print(f"  Scraping episode: {full_title} ({episode_url})")
            m3u8_url = extract_video_link(episode_url)

            if m3u8_url:
                # Basic check to ensure it's a valid looking M3U8 URL
                if m3u8_url.startswith('http') and m3u8_url.endswith('.m3u8'):
                    m3u_entries.append(f'#EXTINF:-1 group-title="{category}",{full_title}\n{m3u8_url}')
                else:
                    print(f"    Warning: Extracted URL doesn't look like a valid M3U8: {m3u8_url}")
            else:
                print(f"    Failed to extract M3U8 for {full_title}.")

    # --- Phase 4: Write M3U file ---
    print(f"\nWriting M3U file to {OUTPUT_PATH}...")
    try:
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for entry in m3u_entries:
                f.write(entry + '\n')
        print(f"Successfully created {OUTPUT_FILENAME} with {len(m3u_entries)} entries.")
    except Exception as e:
        print(f"Error writing M3U file: {e}")

if __name__ == "__main__":
    main()
