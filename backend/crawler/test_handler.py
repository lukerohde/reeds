import time
from unittest.mock import patch, MagicMock
from handler import parse_feed

BLOG = {'author': 'Test Author', 'feed': 'https://example.com/feed.xml'}


def make_entry(title, url, published_parsed=None):
    entry = MagicMock()
    entry.get = lambda k, default=None: {
        'title': title,
        'link': url,
        'published_parsed': published_parsed,
        'updated_parsed': None,
    }.get(k, default)
    return entry


def make_feed(*entries):
    feed = MagicMock()
    feed.entries = list(entries)
    return feed


def test_parse_feed_returns_items():
    entry = make_entry('Hello World', 'https://example.com/hello', time.gmtime(0))
    with patch('feedparser.parse', return_value=make_feed(entry)):
        items = parse_feed(BLOG)
    assert len(items) == 1
    assert items[0]['title'] == 'Hello World'
    assert items[0]['url'] == 'https://example.com/hello'
    assert items[0]['author'] == 'Test Author'
    assert items[0]['served_date'] == ''


def test_parse_feed_skips_entries_without_url():
    entry = make_entry('No URL', '')
    with patch('feedparser.parse', return_value=make_feed(entry)):
        items = parse_feed(BLOG)
    assert items == []


def test_parse_feed_falls_back_to_now_when_no_date():
    entry = make_entry('No Date', 'https://example.com/no-date', published_parsed=None)
    with patch('feedparser.parse', return_value=make_feed(entry)):
        items = parse_feed(BLOG)
    assert len(items) == 1
    assert items[0]['published_date']  # not empty


def test_parse_feed_multiple_entries():
    entries = [
        make_entry(f'Post {i}', f'https://example.com/{i}', time.gmtime(i))
        for i in range(1, 4)
    ]
    with patch('feedparser.parse', return_value=make_feed(*entries)):
        items = parse_feed(BLOG)
    assert len(items) == 3
    assert [i['url'] for i in items] == [
        'https://example.com/1',
        'https://example.com/2',
        'https://example.com/3',
    ]
