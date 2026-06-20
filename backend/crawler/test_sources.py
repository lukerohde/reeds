"""
Unit tests for the pluggable sources — feed parsing, YouTube listing/transcripts,
and the BlogSource / YouTubeSource adapters. No real network, no DynamoDB.

Run via:
    make test
"""
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sources import (
    parse_feed,
    get_recent_videos,
    get_transcript,
    BlogSource,
    YouTubeSource,
)


# ── parse_feed ────────────────────────────────────────────────────────────────

BLOG = {'author': 'Test Author', 'feed': 'https://example.com/feed.xml'}


def _entry(title, url, published_parsed=None):
    entry = MagicMock()
    entry.get = lambda k, default=None: {
        'title':            title,
        'link':             url,
        'published_parsed': published_parsed,
        'updated_parsed':   None,
    }.get(k, default)
    return entry


def _feed(*entries):
    feed = MagicMock()
    feed.entries = list(entries)
    return feed


class TestParseFeed:

    def test_returns_items(self):
        with patch('sources.feedparser.parse', return_value=_feed(_entry('Hello World', 'https://example.com/hello', time.gmtime(0)))):
            items = parse_feed(BLOG)
        assert len(items) == 1
        assert items[0]['title']  == 'Hello World'
        assert items[0]['url']    == 'https://example.com/hello'
        assert items[0]['author'] == 'Test Author'

    def test_skips_entries_without_url(self):
        with patch('sources.feedparser.parse', return_value=_feed(_entry('No URL', ''))):
            assert parse_feed(BLOG) == []

    def test_falls_back_to_now_when_no_date(self):
        with patch('sources.feedparser.parse', return_value=_feed(_entry('No Date', 'https://example.com/no-date'))):
            items = parse_feed(BLOG)
        assert len(items) == 1
        assert items[0]['published_date']  # not empty

    def test_multiple_entries_preserve_order(self):
        entries = [_entry(f'Post {i}', f'https://example.com/{i}', time.gmtime(i)) for i in range(1, 4)]
        with patch('sources.feedparser.parse', return_value=_feed(*entries)):
            items = parse_feed(BLOG)
        assert [i['url'] for i in items] == [
            'https://example.com/1', 'https://example.com/2', 'https://example.com/3',
        ]


# ── get_recent_videos ─────────────────────────────────────────────────────────

def _playlist_response(*videos):
    return {'items': [
        {'snippet': {'title': t, 'publishedAt': p, 'resourceId': {'videoId': v}}}
        for t, v, p in videos
    ]}


def _youtube_mock(response):
    mock = MagicMock()
    mock.playlistItems.return_value.list.return_value.execute.return_value = response
    return mock


class TestGetRecentVideos:

    def test_returns_videos_within_lookback(self):
        now = datetime.now(timezone.utc)
        yt = _youtube_mock(_playlist_response(
            ('Recent', 'vid1', (now - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')),
            ('Old',    'vid2', (now - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')),
        ))
        videos = get_recent_videos(yt, 'UCtest', now - timedelta(days=7), 3)
        assert [v['video_id'] for v in videos] == ['vid1']

    def test_derives_uploads_playlist_id(self):
        yt = _youtube_mock({'items': []})
        get_recent_videos(yt, 'UCsBjURrPoezykLs9EqgamOA', datetime.now(timezone.utc), 3)
        kwargs = yt.playlistItems.return_value.list.call_args.kwargs
        assert kwargs['playlistId'] == 'UUsBjURrPoezykLs9EqgamOA'

    def test_url_format(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        yt     = _youtube_mock(_playlist_response(('My Video', 'abc123', recent)))
        videos = get_recent_videos(yt, 'UCtest', now - timedelta(days=7), 3)
        assert videos[0]['url'] == 'https://www.youtube.com/watch?v=abc123'

    def test_respects_max_videos(self):
        now  = datetime.now(timezone.utc)
        data = [(f'V{i}', f'vid{i}', (now - timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M:%SZ')) for i in range(10)]
        yt   = _youtube_mock(_playlist_response(*data))
        videos = get_recent_videos(yt, 'UCtest', now - timedelta(days=7), 3)
        assert len(videos) == 3

    def test_empty_playlist_returns_empty(self):
        yt = _youtube_mock({'items': []})
        assert get_recent_videos(yt, 'UCtest', datetime.now(timezone.utc) - timedelta(days=7), 3) == []

    def test_stops_at_first_old_video(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        old    = (now - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        yt = _youtube_mock(_playlist_response(
            ('Recent', 'new1', recent),
            ('Old',    'old1', old),
            ('After',  'new2', recent),
        ))
        ids = [v['video_id'] for v in get_recent_videos(yt, 'UCtest', now - timedelta(days=7), 3)]
        assert 'old1' not in ids and 'new2' not in ids


# ── get_transcript ────────────────────────────────────────────────────────────

class TestGetTranscript:

    def test_returns_joined_text(self):
        with patch('sources.YouTubeTranscriptApi') as cls:
            cls.return_value.fetch.return_value = [MagicMock(text='Hello world'), MagicMock(text='This is great')]
            assert get_transcript('abc') == 'Hello world This is great'

    def test_falls_back_to_list_when_fetch_empty(self):
        t = MagicMock()
        t.fetch.return_value = [MagicMock(text='Fallback text')]
        with patch('sources.YouTubeTranscriptApi') as cls:
            cls.return_value.fetch.return_value = []
            cls.return_value.list.return_value  = [t]
            assert get_transcript('abc') == 'Fallback text'

    def test_returns_empty_when_unavailable(self):
        with patch('sources.YouTubeTranscriptApi') as cls:
            cls.return_value.fetch.side_effect = Exception('disabled')
            cls.return_value.list.side_effect  = Exception('also disabled')
            assert get_transcript('vid') == ''

    def test_logs_error_on_failure(self, capsys):
        with patch('sources.YouTubeTranscriptApi') as cls:
            cls.return_value.fetch.side_effect = Exception('TranscriptsDisabled')
            cls.return_value.list.side_effect  = Exception('also failed')
            get_transcript('errVid')
        out = capsys.readouterr().out
        assert 'errVid' in out and 'TranscriptsDisabled' in out

    def test_passes_video_id_to_fetch(self):
        with patch('sources.YouTubeTranscriptApi') as cls:
            cls.return_value.fetch.return_value = []
            cls.return_value.list.return_value  = []
            get_transcript('myVideoId')
        cls.return_value.fetch.assert_called_once_with('myVideoId')


# ── BlogSource ────────────────────────────────────────────────────────────────

class TestBlogSource:

    def test_discover_aggregates_all_blogs(self):
        src = BlogSource([
            {'author': 'A', 'feed': 'fa'},
            {'author': 'B', 'feed': 'fb'},
        ])
        with patch('sources.feedparser.parse', side_effect=[
            _feed(_entry('A1', 'https://a/1', time.gmtime(1))),
            _feed(_entry('B1', 'https://b/1', time.gmtime(2))),
        ]):
            items = src.discover()
        assert {i['author'] for i in items} == {'A', 'B'}

    def test_fetch_content_cleans_html(self):
        src = BlogSource([])
        resp = MagicMock(text='<html><body><script>x</script><p>Hello world</p></body></html>')
        with patch('sources.requests.get', return_value=resp):
            text = src.fetch_content({'url': 'https://example.com'})
        assert 'Hello world' in text
        assert 'x' not in text.split()           # script stripped

    def test_fetch_content_returns_empty_on_error(self):
        src = BlogSource([])
        with patch('sources.requests.get', side_effect=Exception('boom')):
            assert src.fetch_content({'url': 'https://example.com'}) == ''


# ── YouTubeSource ─────────────────────────────────────────────────────────────

class TestYouTubeSource:

    def _src(self):
        return YouTubeSource(
            youtubers=[{'name': 'Chan', 'channel_id': 'UCx'}],
            api_key='real-key', lookback_days=7, max_per_channel=3,
        )

    def test_discover_returns_youtube_items(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        yt     = _youtube_mock(_playlist_response(('Vid', 'v1', recent)))
        with patch('sources.build', return_value=yt):
            items = self._src().discover()
        assert len(items) == 1
        assert items[0]['source']   == 'youtube'
        assert items[0]['video_id'] == 'v1'
        assert items[0]['author']   == 'Chan'

    def test_discover_empty_without_api_key(self):
        src = YouTubeSource([{'name': 'C', 'channel_id': 'UCx'}], api_key='', lookback_days=7, max_per_channel=3)
        assert src.discover() == []

    def test_discover_continues_when_one_channel_errors(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        src = YouTubeSource(
            youtubers=[{'name': 'Bad', 'channel_id': 'UCbad'}, {'name': 'Good', 'channel_id': 'UCgood'}],
            api_key='real-key', lookback_days=7, max_per_channel=3,
        )
        calls = [0]

        def grv(youtube, channel_id, since, max_videos):
            calls[0] += 1
            if calls[0] == 1:
                raise Exception('quota exceeded')
            return [{'video_id': 'ok', 'url': 'https://youtu.be/ok', 'title': 'Good', 'published_date': recent}]

        with patch('sources.build', return_value=MagicMock()), \
             patch('sources.get_recent_videos', side_effect=grv):
            items = src.discover()
        assert [i['video_id'] for i in items] == ['ok']

    def test_fetch_content_returns_transcript(self):
        src = self._src()
        with patch('sources.get_transcript', return_value='the transcript') as gt:
            assert src.fetch_content({'video_id': 'v1'}) == 'the transcript'
        gt.assert_called_once_with('v1')
