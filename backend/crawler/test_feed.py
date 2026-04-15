"""
Test whether a URL is a parseable feed, or discover the feed URL from a homepage.

Usage:
    python test_feed.py <url>

If the URL is a homepage (HTML), attempts to discover the feed via <link> tags
and common URL patterns, then shows what the crawler would store.
"""

import sys
import requests
from bs4 import BeautifulSoup
from handler import parse_feed

FEED_TYPES = {'application/rss+xml', 'application/atom+xml', 'application/feed+json'}
COMMON_PATHS = ['/feed', '/rss', '/atom.xml', '/feed.xml', '/index.xml', '/rss.xml', '/feeds/posts/default']


def discover_feed(url):
    """
    Return (feed_url, method) where method describes how it was found.
    Returns (None, reason) if no feed could be found.
    """
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'reeds-digest/1.0'})
    except Exception as e:
        return None, f"could not fetch {url}: {e}"

    # If the response looks like a feed already, use it directly
    ct = r.headers.get('Content-Type', '')
    if any(t in ct for t in ('xml', 'json', 'rss', 'atom')):
        return url, 'URL is already a feed'

    # Parse HTML and look for <link rel="alternate"> feed declarations
    soup = BeautifulSoup(r.text, 'html.parser')
    for link in soup.find_all('link', rel='alternate'):
        if link.get('type', '') in FEED_TYPES and link.get('href'):
            href = link['href']
            if href.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            return href, f'discovered via <link rel="alternate"> ({link.get("type")})'

    # Try common URL patterns
    from urllib.parse import urlparse
    base = urlparse(url)
    base_url = f"{base.scheme}://{base.netloc}"
    for path in COMMON_PATHS:
        candidate = base_url + path
        try:
            resp = requests.get(candidate, timeout=5, headers={'User-Agent': 'reeds-digest/1.0'})
            ct = resp.headers.get('Content-Type', '')
            if resp.status_code == 200 and any(t in ct for t in ('xml', 'json', 'rss', 'atom')):
                return candidate, f'found at common path {path}'
        except Exception:
            continue

    return None, 'no feed found via <link> tags or common paths'


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python test_feed.py <url>")
        sys.exit(1)

    url = sys.argv[1]

    feed_url, method = discover_feed(url)
    if not feed_url:
        print(f"❌  {method}")
        sys.exit(1)

    print(f"✅  Feed: {feed_url}")
    print(f"   ({method})")
    print()

    items = parse_feed({'author': 'test', 'feed': feed_url})
    if not items:
        print("⚠️   Feed parsed but no entries found")
        sys.exit(1)

    print(f"Entries found: {len(items)}")
    for item in items[:5]:
        print(f"  [{item['published_date'][:10]}] {item['title']}")
        print(f"    {item['url']}")

    print()
    print("Add to config/config.yaml:")
    print(f"  - author: <Author Name>")
    print(f"    url: {url.rstrip('/')}")
    print(f"    feed: {feed_url}")
