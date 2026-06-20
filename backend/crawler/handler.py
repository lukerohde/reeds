"""
reeds crawler — pure extract.

Iterates over plug-and-play content Sources (blogs, YouTube, …), storing new
items in DynamoDB. There is no AI here: extraction is cheap, fast, and testable.
Every source produces the same item shape, so the store/retry loop below is
completely source-agnostic.
"""
import os
import yaml
import boto3
from pathlib import Path
from datetime import datetime, timezone

from sources import BlogSource, YouTubeSource

_cfg          = yaml.safe_load((Path(__file__).parent / 'config.yaml').read_text())
CONTENT_LIMIT = _cfg['settings']['content_limit']

TABLE_NAME = os.environ['DYNAMODB_TABLE']

dynamodb = boto3.resource('dynamodb')
table    = dynamodb.Table(TABLE_NAME)


def build_sources(cfg):
    """Assemble the enabled content sources. YouTube is opt-in: it needs both
    configured channels and a YOUTUBE_API_KEY."""
    sources   = [BlogSource(cfg.get('blogs', []))]
    youtubers = cfg.get('youtubers', [])
    api_key   = os.environ.get('YOUTUBE_API_KEY', '')
    if youtubers and api_key:
        s = cfg.get('settings', {})
        sources.append(YouTubeSource(
            youtubers,
            api_key,
            s.get('youtube_lookback_days', 7),
            s.get('max_videos_per_channel', 3),
        ))
    return sources


def _content_and_count(text):
    """Truncate to the storage limit and count words — the one place that turns
    raw source text into the (content, word_count) pair we persist."""
    return text[:CONTENT_LIMIT], len(text.split())


def _needs_content_retry(existing):
    """True for an unserved item we stored without content — worth re-fetching
    next run (a blog whose fetch failed, or a video whose captions weren't ready)."""
    return existing.get('served_date') == '' and not existing.get('content', '').strip()


def _store_new(source, item):
    """Fetch content for a brand-new item and persist it."""
    item['content'], item['word_count'] = _content_and_count(source.fetch_content(item))
    item.setdefault('fetched_date', datetime.now(timezone.utc).isoformat())
    item.setdefault('served_date', '')
    item.setdefault('source', source.name)
    table.put_item(Item=item)


def _retry_content(source, item):
    """Re-fetch content for an item stored without any. On success, clears
    status/summary so the digest re-processes it. Returns True if updated."""
    text = source.fetch_content(item)
    if not text:
        return False
    content, word_count = _content_and_count(text)
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='SET content = :c, word_count = :w, #s = :st, summary = :sm',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':c': content, ':w': word_count, ':st': '', ':sm': ''},
    )
    return True


def crawl(sources):
    """Discover items from every source, store the new ones, and retry content
    for unserved items that still lack it. The loop is identical for all sources."""
    added = 0
    for source in sources:
        for item in source.discover():
            existing = table.get_item(Key={'url': item['url']}).get('Item')
            if existing:
                if _needs_content_retry(existing) and _retry_content(source, item):
                    print(f"  [retry]   {item['author']}: {item['title']}")
                continue
            _store_new(source, item)
            print(f"  [fetched] {item['author']}: {item['title']} ({item['word_count']} words)")
            added += 1
    return {'added': added}


def handler(event, context):
    return crawl(build_sources(_cfg))
