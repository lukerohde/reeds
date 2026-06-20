"""
Unit tests for the crawler's source-agnostic store/retry loop and source assembly.
No network, no DynamoDB — a FakeSource drives crawl() and the table is mocked.

Run via:
    make test
"""
import os

os.environ.setdefault('DYNAMODB_TABLE', 'test-table')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'test')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'test')

from unittest.mock import patch, MagicMock

import handler as h
from handler import crawl, build_sources


class FakeSource:
    """A Source stand-in that returns canned items and content."""
    name = 'fake'

    def __init__(self, items, content='body text'):
        self._items   = items
        self._content = content
        self.fetched  = []

    def discover(self):
        return list(self._items)

    def fetch_content(self, item):
        self.fetched.append(item['url'])
        return self._content


def _item(url='https://example.com/a', author='Alice', title='A post'):
    return {'url': url, 'author': author, 'title': title, 'published_date': '2024-01-01T00:00:00Z'}


# ── crawl(): storing new items ────────────────────────────────────────────────

class TestCrawlStore:

    def test_stores_new_item_with_schema(self):
        table = MagicMock()
        table.get_item.return_value = {}          # no existing item
        src = FakeSource([_item()], content='hello world here')
        with patch.object(h, 'table', table):
            result = crawl([src])
        assert result == {'added': 1}
        item = table.put_item.call_args[1]['Item']
        assert item['content']     == 'hello world here'
        assert item['word_count']  == 3
        assert item['served_date'] == ''
        assert item['source']      == 'fake'

    def test_truncates_content_to_limit(self):
        table = MagicMock()
        table.get_item.return_value = {}
        src = FakeSource([_item()], content='x' * 10000)
        with patch.object(h, 'table', table), patch.object(h, 'CONTENT_LIMIT', 100):
            crawl([src])
        item = table.put_item.call_args[1]['Item']
        assert len(item['content']) == 100

    def test_preserves_source_specific_fields(self):
        table = MagicMock()
        table.get_item.return_value = {}
        yt_item = {**_item(url='https://youtube.com/watch?v=x'), 'source': 'youtube', 'video_id': 'x'}
        src = FakeSource([yt_item], content='transcript')
        with patch.object(h, 'table', table):
            crawl([src])
        item = table.put_item.call_args[1]['Item']
        assert item['source']   == 'youtube'    # not overwritten by setdefault
        assert item['video_id'] == 'x'


# ── crawl(): existing items, dedup + content retry ───────────────────────────

class TestCrawlExisting:

    def test_skips_existing_item_with_content(self):
        table = MagicMock()
        table.get_item.return_value = {'Item': {'url': 'x', 'served_date': '', 'content': 'already here'}}
        src = FakeSource([_item()])
        with patch.object(h, 'table', table):
            result = crawl([src])
        assert result == {'added': 0}
        table.put_item.assert_not_called()
        table.update_item.assert_not_called()
        assert src.fetched == []                 # no content re-fetch

    def test_retries_content_for_unserved_empty_item(self):
        table = MagicMock()
        table.get_item.return_value = {'Item': {'url': 'x', 'served_date': '', 'content': ''}}
        src = FakeSource([_item()], content='now we have text')
        with patch.object(h, 'table', table):
            result = crawl([src])
        assert result == {'added': 0}            # not a new item
        table.update_item.assert_called_once()
        vals = table.update_item.call_args[1]['ExpressionAttributeValues']
        assert vals[':c']  == 'now we have text'
        assert vals[':st'] == ''                 # status cleared so digest re-processes
        assert vals[':sm'] == ''

    def test_retry_noop_when_content_still_unavailable(self):
        table = MagicMock()
        table.get_item.return_value = {'Item': {'url': 'x', 'served_date': '', 'content': ''}}
        src = FakeSource([_item()], content='')   # still nothing
        with patch.object(h, 'table', table):
            crawl([src])
        table.update_item.assert_not_called()

    def test_skips_retry_for_served_item(self):
        table = MagicMock()
        table.get_item.return_value = {'Item': {'url': 'x', 'served_date': '2026-01-01', 'content': ''}}
        src = FakeSource([_item()], content='text')
        with patch.object(h, 'table', table):
            crawl([src])
        table.update_item.assert_not_called()
        assert src.fetched == []


# ── pure helpers ──────────────────────────────────────────────────────────────

class TestContentAndCount:

    def test_counts_words(self):
        assert h._content_and_count('one two three') == ('one two three', 3)

    def test_empty_text(self):
        assert h._content_and_count('') == ('', 0)

    def test_truncates_to_limit(self):
        with patch.object(h, 'CONTENT_LIMIT', 5):
            content, _ = h._content_and_count('abcdefgh')
        assert content == 'abcde'


class TestNeedsContentRetry:
    """An item is worth re-fetching only if it's unserved AND has no content yet."""

    def test_unserved_without_content(self):
        assert h._needs_content_retry({'served_date': '', 'content': ''})

    def test_unserved_with_whitespace_only_content(self):
        assert h._needs_content_retry({'served_date': '', 'content': '   '})

    def test_unserved_with_real_content(self):
        assert not h._needs_content_retry({'served_date': '', 'content': 'body'})

    def test_already_served_never_retries(self):
        assert not h._needs_content_retry({'served_date': '2026-01-01', 'content': ''})


# ── build_sources(): plug-and-play assembly ──────────────────────────────────

class TestBuildSources:

    _cfg = {'blogs': [{'author': 'A', 'url': 'u', 'feed': 'f'}],
            'youtubers': [{'name': 'Chan', 'channel_id': 'UCx'}],
            'settings': {}}

    def test_blog_source_always_present(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('YOUTUBE_API_KEY', None)
            sources = build_sources(self._cfg)
        assert [s.name for s in sources] == ['blog']

    def test_youtube_added_when_key_and_channels_present(self):
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': 'real-key'}):
            sources = build_sources(self._cfg)
        assert [s.name for s in sources] == ['blog', 'youtube']

    def test_youtube_omitted_without_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('YOUTUBE_API_KEY', None)
            sources = build_sources(self._cfg)
        assert [s.name for s in sources] == ['blog']

    def test_youtube_omitted_without_channels(self):
        cfg = {'blogs': [], 'youtubers': [], 'settings': {}}
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': 'real-key'}):
            sources = build_sources(cfg)
        assert [s.name for s in sources] == ['blog']
