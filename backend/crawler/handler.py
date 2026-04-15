import os
import yaml
import boto3
import feedparser
import requests
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup

_cfg          = yaml.safe_load((Path(__file__).parent / 'config.yaml').read_text())
BLOGS         = _cfg['blogs']
CONTENT_LIMIT = _cfg['settings']['content_limit']

TABLE_NAME = os.environ['DYNAMODB_TABLE']

dynamodb = boto3.resource('dynamodb')
table    = dynamodb.Table(TABLE_NAME)


def parse_feed(blog):
    """Parse a feed and return article metadata items."""
    feed = feedparser.parse(blog['feed'])
    items = []
    for entry in feed.entries:
        url = entry.get('link', '')
        if not url:
            continue
        published = entry.get('published_parsed') or entry.get('updated_parsed')
        published_date = (
            datetime(*published[:6], tzinfo=timezone.utc).isoformat()
            if published
            else datetime.now(timezone.utc).isoformat()
        )
        items.append({
            'url': url,
            'author': blog['author'],
            'title': entry.get('title', 'Untitled'),
            'published_date': published_date,
            'fetched_date': datetime.now(timezone.utc).isoformat(),
            'served_date': '',
        })
    return items


def fetch_content(url):
    """Fetch and clean article text. Returns (word_count, text)."""
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'reeds-digest/1.0'})
        soup = BeautifulSoup(r.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return len(text.split()), text
    except Exception:
        return 0, ''


def handler(event, context):
    added = 0
    for blog in BLOGS:
        for item in parse_feed(blog):
            if 'Item' in table.get_item(Key={'url': item['url']}):
                continue
            word_count, text = fetch_content(item['url'])
            item['word_count'] = word_count
            item['content']    = text[:CONTENT_LIMIT]
            table.put_item(Item=item)
            print(f"  [fetched] {item['author']}: {item['title']} ({word_count} words)")
            added += 1
    return {'added': added}


def test_feed(feed_url):
    """Parse a feed URL and print what the crawler would store. No DDB writes."""
    items = parse_feed({'author': 'test', 'feed': feed_url})
    print(f"Entries found: {len(items)}")
    for item in items[:5]:
        print(f"  [{item['published_date'][:10]}] {item['title']}")
        print(f"    {item['url']}")
