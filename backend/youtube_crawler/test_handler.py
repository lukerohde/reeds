"""
Unit tests for the YouTube crawler handler — no real YouTube API, no DynamoDB.

Run via:
    make test-youtube
"""
import os

os.environ.setdefault('DYNAMODB_TABLE', 'test-table')
os.environ.setdefault('YOUTUBE_API_KEY', 'test-key')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'test')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'test')

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

from handler import get_recent_videos, get_transcript


def _playlist_response(*videos):
    """Build a fake playlistItems.list() response.

    Each video is a (title, video_id, published_iso) tuple.
    """
    return {
        'items': [
            {
                'snippet': {
                    'title':      title,
                    'publishedAt': published,
                    'resourceId': {'videoId': video_id},
                }
            }
            for title, video_id, published in videos
        ]
    }


def _youtube_mock(response):
    """Return a mock YouTube client whose playlistItems().list().execute() returns response."""
    mock = MagicMock()
    mock.playlistItems.return_value.list.return_value.execute.return_value = response
    return mock


# ── TestGetRecentVideos ───────────────────────────────────────────────────────

class TestGetRecentVideos:

    def test_returns_videos_within_lookback(self):
        now          = datetime.now(timezone.utc)
        two_days_ago = (now - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        ten_days_ago = (now - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')

        yt = _youtube_mock(_playlist_response(
            ('Recent video', 'vid1', two_days_ago),
            ('Old video',    'vid2', ten_days_ago),
        ))
        since  = now - timedelta(days=7)
        videos = get_recent_videos(yt, 'UCtest', since)

        assert len(videos) == 1
        assert videos[0]['video_id'] == 'vid1'

    def test_derives_uploads_playlist_id(self):
        """Uploads playlist ID is channel ID with 'UC' → 'UU'."""
        yt = _youtube_mock({'items': []})
        get_recent_videos(yt, 'UCsBjURrPoezykLs9EqgamOA', datetime.now(timezone.utc))
        yt.playlistItems.return_value.list.assert_called_once()
        kwargs = yt.playlistItems.return_value.list.call_args.kwargs
        assert kwargs['playlistId'] == 'UUsBjURrPoezykLs9EqgamOA'

    def test_url_format(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        yt     = _youtube_mock(_playlist_response(('My Video', 'abc123', recent)))
        since  = now - timedelta(days=7)
        videos = get_recent_videos(yt, 'UCtest', since)
        assert videos[0]['url'] == 'https://www.youtube.com/watch?v=abc123'

    def test_respects_max_videos_per_channel(self):
        """At most MAX_VIDEOS_PER_CHANNEL videos returned even if more are recent."""
        now    = datetime.now(timezone.utc)
        since  = now - timedelta(days=7)
        data   = [
            (f'Video {i}', f'vid{i}', (now - timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M:%SZ'))
            for i in range(10)
        ]
        yt     = _youtube_mock(_playlist_response(*data))
        videos = get_recent_videos(yt, 'UCtest', since)
        # MAX_VIDEOS_PER_CHANNEL comes from config (currently 3)
        assert len(videos) <= 3

    def test_empty_playlist_returns_empty_list(self):
        yt = _youtube_mock({'items': []})
        videos = get_recent_videos(yt, 'UCtest', datetime.now(timezone.utc) - timedelta(days=7))
        assert videos == []

    def test_stops_at_first_old_video(self):
        """Iteration should stop as soon as a video older than since is found."""
        now    = datetime.now(timezone.utc)
        since  = now - timedelta(days=7)
        recent = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        old    = (now - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        # Second video is old — third should never be reached
        yt     = _youtube_mock(_playlist_response(
            ('Recent', 'new1', recent),
            ('Old',    'old1', old),
            ('After',  'new2', recent),  # would be recent but iteration stopped
        ))
        videos = get_recent_videos(yt, 'UCtest', since)
        video_ids = [v['video_id'] for v in videos]
        assert 'old1' not in video_ids
        assert 'new2' not in video_ids  # iteration halted at old video


# ── TestGetTranscript ─────────────────────────────────────────────────────────

class TestGetTranscript:
    """get_transcript returns joined caption text, or '' when unavailable."""

    def test_returns_joined_text(self):
        mock_snippets = [MagicMock(text='Hello world'), MagicMock(text='This is great')]
        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            mock_cls.return_value.fetch.return_value = mock_snippets
            result = get_transcript('abc123')
        assert result == 'Hello world This is great'

    def test_returns_empty_when_transcript_unavailable(self):
        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            mock_cls.return_value.fetch.side_effect = Exception('disabled')
            mock_cls.return_value.list.side_effect  = Exception('also disabled')
            result = get_transcript('vid')
        assert result == ''

    def test_logs_error_when_fetch_fails(self, capsys):
        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            mock_cls.return_value.fetch.side_effect = Exception('TranscriptsDisabled')
            mock_cls.return_value.list.side_effect  = Exception('also failed')
            get_transcript('errVid')
        out = capsys.readouterr().out
        assert 'errVid' in out
        assert 'TranscriptsDisabled' in out

    def test_passes_video_id_to_fetch(self):
        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            mock_cls.return_value.fetch.return_value = []
            mock_cls.return_value.list.return_value = []
            get_transcript('myVideoId')
        mock_cls.return_value.fetch.assert_called_once_with('myVideoId')

    def test_falls_back_to_list_when_fetch_returns_empty(self):
        """If fetch() returns empty text, list() is tried to find any available transcript."""
        fallback_snippets = [MagicMock(text='Fallback transcript text')]
        mock_transcript   = MagicMock()
        mock_transcript.fetch.return_value = fallback_snippets

        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            instance = mock_cls.return_value
            instance.fetch.return_value = []       # fetch returns empty
            instance.list.return_value  = [mock_transcript]
            result = get_transcript('abc123')

        assert result == 'Fallback transcript text'

    def test_returns_empty_when_both_fetch_and_list_fail(self):
        """If fetch raises and list also raises, returns ''."""
        with patch('handler.YouTubeTranscriptApi') as mock_cls:
            mock_cls.return_value.fetch.side_effect = Exception('disabled')
            mock_cls.return_value.list.side_effect  = Exception('also disabled')
            result = get_transcript('vid')
        assert result == ''


# ── TestHandler ───────────────────────────────────────────────────────────────

class TestHandler:

    def test_no_youtubers_returns_zero(self):
        with patch('handler.YOUTUBERS', []):
            from handler import handler
            result = handler({}, None)
        assert result == {'stored': 0}

    def test_stores_new_video_with_correct_schema(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        yt = _youtube_mock(_playlist_response(('New Video', 'vid999', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.get_transcript', return_value=''), \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            result = handler({}, None)

        assert result['stored'] == 1
        item = mock_table.put_item.call_args[1]['Item']
        assert item['source'] == 'youtube'
        assert item['served_date'] == ''
        assert 'video_id' in item
        assert item['url'] == 'https://www.youtube.com/watch?v=vid999'
        assert item['author'] == 'TestChannel'

    def test_stores_transcript_in_content(self):
        """Handler fetches transcript and stores it in the content field."""
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        yt = _youtube_mock(_playlist_response(('Video with captions', 'vid_caps', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.get_transcript', return_value='This is the transcript.') as mock_gt, \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            handler({}, None)

        mock_gt.assert_called_once_with('vid_caps')
        item = mock_table.put_item.call_args[1]['Item']
        assert item['content'] == 'This is the transcript.'
        assert item['word_count'] == 4

    def test_skips_existing_videos(self):
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {'Item': {'url': 'already-there'}}

        yt = _youtube_mock(_playlist_response(('Existing', 'vid_existing', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            result = handler({}, None)

        assert result['stored'] == 0
        mock_table.put_item.assert_not_called()

    def test_retries_transcript_for_existing_no_content_video(self):
        """A stored video with no transcript is retried on next crawl run."""
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {'Item': {
            'url':         'https://www.youtube.com/watch?v=vid_retry',
            'served_date': '',
            'content':     '',
        }}

        yt = _youtube_mock(_playlist_response(('Retry Video', 'vid_retry', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.get_transcript', return_value='Now we have a transcript.') as mock_gt, \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            result = handler({}, None)

        assert result['stored'] == 0          # not a new item
        mock_gt.assert_called_once_with('vid_retry')
        mock_table.update_item.assert_called_once()

    def test_clears_status_when_transcript_retry_succeeds(self):
        """When a retry fetches a transcript, status is cleared so the digest re-processes."""
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {'Item': {
            'url':         'https://www.youtube.com/watch?v=vid_clear',
            'served_date': '',
            'content':     '',
            'status':      'relevant',
            'summary':     '',
        }}

        yt = _youtube_mock(_playlist_response(('Clear Video', 'vid_clear', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.get_transcript', return_value='Got transcript.'), \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            handler({}, None)

        kw = mock_table.update_item.call_args[1]
        vals = kw['ExpressionAttributeValues']
        assert vals.get(':st') == ''   # status cleared
        assert vals.get(':sm') == ''   # summary cleared

    def test_skips_retry_for_served_videos(self):
        """Videos already served should not have their transcript retried."""
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {'Item': {
            'url':         'https://www.youtube.com/watch?v=vid_served',
            'served_date': '2026-01-01',
            'content':     '',
        }}

        yt = _youtube_mock(_playlist_response(('Served Video', 'vid_served', recent)))

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=yt), \
             patch('handler.get_transcript') as mock_gt, \
             patch('handler.YOUTUBERS', [{'name': 'TestChannel', 'channel_id': 'UCtest'}]):
            from handler import handler
            handler({}, None)

        mock_gt.assert_not_called()
        mock_table.update_item.assert_not_called()

    def test_api_error_skips_channel_and_continues(self):
        """An API error on one channel should not abort processing of others."""
        now    = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        bad_yt  = MagicMock()
        good_yt = MagicMock()
        # First channel errors, second succeeds
        bad_yt.playlistItems.return_value.list.return_value.execute.side_effect = Exception('quota exceeded')
        good_yt.playlistItems.return_value.list.return_value.execute.return_value = \
            _playlist_response(('Good video', 'vidOK', recent))

        call_count = [0]

        def build_side_effect(*a, **kw):
            call_count[0] += 1
            return bad_yt if call_count[0] == 1 else good_yt

        # build() is called once per handler invocation, not per channel, so patch
        # get_recent_videos to simulate the error for the first channel only
        original_grv = __import__('handler').get_recent_videos
        call_grv = [0]

        def grv_side_effect(youtube, channel_id, since):
            call_grv[0] += 1
            if call_grv[0] == 1:
                raise Exception('quota exceeded')
            return original_grv(good_yt, channel_id, since)

        with patch('handler.table', mock_table), \
             patch('handler.build', return_value=bad_yt), \
             patch('handler.get_recent_videos', side_effect=grv_side_effect), \
             patch('handler.YOUTUBERS', [
                 {'name': 'Bad Channel',  'channel_id': 'UCbad'},
                 {'name': 'Good Channel', 'channel_id': 'UCgood'},
             ]):
            from handler import handler
            result = handler({}, None)

        assert result['stored'] == 1
