"""
Unit tests for digest handler pure functions — no DynamoDB, no AI required.

Run via:
    make test-digest
"""
import os
from unittest.mock import patch, MagicMock

# Set env vars before importing the handler so module-level assignments don't KeyError
os.environ.setdefault('DYNAMODB_TABLE', 'test-table')
os.environ.setdefault('BUCKET_NAME', 'test-bucket')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-west-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'test')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'test')

import handler as _h
from handler import select_candidates, read_time_label, build_html


def _art(author, i=0):
    return {
        'url':            f'https://example.com/{author.lower().replace(" ", "-")}/{i}',
        'author':         author,
        'title':          f'{author} post {i}',
        'published_date': f'2024-01-{max(1, 15 - i):02d}T00:00:00Z',
        'summary':        f'Summary {i}',
    }


# ── TestSelectCandidates ──────────────────────────────────────────────────────

class TestSelectCandidates:

    def test_no_cap_returns_first_n(self):
        articles = [_art('Alice', i) for i in range(30)]
        result = select_candidates(articles, pool_size=20, max_per_author=0)
        assert len(result) == 20
        assert result == articles[:20]

    def test_cap_limits_single_dominant_author(self):
        # 20 Simon articles — cap of 2 should yield only 2
        articles = [_art('Simon', i) for i in range(20)]
        result = select_candidates(articles, pool_size=20, max_per_author=2)
        assert len(result) == 2
        assert all(a['author'] == 'Simon' for a in result)

    def test_cap_enables_diversity(self):
        # 15 Simon (most recent) + 10 other authors — cap=2 lets others fill the pool
        articles = [_art('Simon', i) for i in range(15)]
        articles += [_art(f'Author{i}', 0) for i in range(10)]
        result = select_candidates(articles, pool_size=10, max_per_author=2)
        assert len(result) == 10
        simon_count = sum(1 for a in result if a['author'] == 'Simon')
        assert simon_count == 2

    def test_pool_size_is_respected(self):
        articles = [_art(f'Author{i}', 0) for i in range(30)]
        result = select_candidates(articles, pool_size=10, max_per_author=3)
        assert len(result) == 10

    def test_fewer_available_than_pool(self):
        # 3 Alice articles, cap=2, pool=20 → only 2 pass the cap
        articles = [_art('Alice', i) for i in range(3)]
        result = select_candidates(articles, pool_size=20, max_per_author=2)
        assert len(result) == 2

    def test_cap_zero_disables_cap(self):
        articles = [_art('Simon', i) for i in range(25)]
        result = select_candidates(articles, pool_size=20, max_per_author=0)
        assert len(result) == 20
        assert all(a['author'] == 'Simon' for a in result)

    def test_preserves_recency_order(self):
        # Articles are assumed sorted by date desc; cap picks the most recent ones
        articles = [_art('Simon', i) for i in range(5)] + [_art('Alice', i) for i in range(5)]
        result = select_candidates(articles, pool_size=10, max_per_author=2)
        simon_results = [a for a in result if a['author'] == 'Simon']
        assert simon_results[0]['url'] == _art('Simon', 0)['url']  # most recent first
        assert simon_results[1]['url'] == _art('Simon', 1)['url']

    def test_empty_input_returns_empty(self):
        assert select_candidates([], pool_size=20, max_per_author=2) == []

    def test_mixed_authors_all_under_cap(self):
        # 3 authors × 1 article each — all should be included
        articles = [_art('Alice', 0), _art('Bob', 0), _art('Carol', 0)]
        result = select_candidates(articles, pool_size=10, max_per_author=2)
        assert len(result) == 3


# ── TestReadTimeLabel ─────────────────────────────────────────────────────────

class TestReadTimeLabel:

    def test_zero_returns_empty(self):
        assert read_time_label(0) == ''

    def test_none_returns_empty(self):
        assert read_time_label(None) == ''

    def test_very_short_article(self):
        assert read_time_label(50) == '< 1 min read'

    def test_standard_article(self):
        # 600 words / 200 wpm = 3 minutes
        assert read_time_label(600) == '3 min read'

    def test_string_word_count(self):
        # DynamoDB stores numbers as Decimal or string
        assert read_time_label('400') == '2 min read'


# ── TestBuildHtml ─────────────────────────────────────────────────────────────

class TestBuildHtml:

    _article = {
        'url':            'https://example.com/article',
        'title':          'My Article Title',
        'author':         'Alice',
        'published_date': '2024-01-15T00:00:00Z',
        'summary':        'Key insight here.',
    }

    def test_contains_title_and_url(self):
        html = build_html([self._article], '2024-01-15', None)
        assert 'My Article Title' in html
        assert 'https://example.com/article' in html

    def test_contains_author(self):
        html = build_html([self._article], '2024-01-15', None)
        assert 'Alice' in html

    def test_contains_summary(self):
        html = build_html([self._article], '2024-01-15', None)
        assert 'Key insight here.' in html

    def test_prev_link_when_given(self):
        html = build_html([self._article], '2024-01-15', '2024-01-14')
        assert '← previous' in html
        assert '/digest/2024-01-14/' in html

    def test_no_prev_link_when_none(self):
        html = build_html([self._article], '2024-01-15', None)
        assert '← previous' not in html

    def test_heading_contains_date(self):
        html = build_html([self._article], '2024-01-15', None)
        assert '2024-01-15' in html

    def test_bold_markdown_rendered_as_strong(self):
        article = {**self._article, 'summary': '**TLDR:** Key insight here.'}
        html = build_html([article], '2024-01-15', None)
        assert '<strong>TLDR:</strong>' in html
        assert '**TLDR:**' not in html

    def test_italic_markdown_rendered_as_em(self):
        article = {**self._article, 'summary': 'Something *important* here.'}
        html = build_html([article], '2024-01-15', None)
        assert '<em>important</em>' in html
        assert '*important*' not in html


# ── TestMakeSummaryWordCount ──────────────────────────────────────────────────

class TestMakeSummaryWordCount:
    """make_summary selects verbatim/excerpt for short articles, TLDR for long ones."""

    def _mock_response(self, text):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        return resp

    def test_short_content_prompt_is_explanatory(self):
        with patch.object(_h, 'ai') as mock_ai:
            mock_ai.messages.create.return_value = self._mock_response('Explanation.')
            _h.make_summary('Title', 'Author', 'Short article.', word_count=100)
        content = mock_ai.messages.create.call_args[1]['messages'][0]['content']
        assert any(w in content.lower() for w in ('explain', 'sentence', 'substance', 'matters'))

    def test_long_content_prompt_uses_tldr_approach(self):
        with patch.object(_h, 'ai') as mock_ai:
            mock_ai.messages.create.return_value = self._mock_response('TLDR.')
            _h.make_summary('Title', 'Author', 'Long article. ' * 100, word_count=600)
        content = mock_ai.messages.create.call_args[1]['messages'][0]['content']
        assert any(w in content.lower() for w in ('tldr', 'distil', 'distill', 'insight'))

    def test_missing_word_count_defaults_to_short_approach(self):
        with patch.object(_h, 'ai') as mock_ai:
            mock_ai.messages.create.return_value = self._mock_response('Summary.')
            _h.make_summary('Title', 'Author', 'Content.')
        content = mock_ai.messages.create.call_args[1]['messages'][0]['content']
        assert any(w in content.lower() for w in ('explain', 'sentence', 'substance', 'matters'))

    def test_exactly_at_threshold_uses_long_approach(self):
        with patch.object(_h, 'ai') as mock_ai:
            mock_ai.messages.create.return_value = self._mock_response('TLDR.')
            _h.make_summary('Title', 'Author', 'Content', word_count=_h.SUMMARISE_LONG_THRESHOLD)
        content = mock_ai.messages.create.call_args[1]['messages'][0]['content']
        assert any(w in content.lower() for w in ('tldr', 'distil', 'distill', 'insight'))


# ── TestGeminiSummariseVideo ──────────────────────────────────────────────────

class TestGeminiSummariseVideo:
    """gemini_summarise_video calls Gemini REST endpoint and returns the summary text."""

    def test_returns_summary_on_success(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'candidates': [{'content': {'parts': [{'text': 'Great video.'}]}}]}
        with patch.object(_h, 'GEMINI_API_KEY', 'test-key'), \
             patch.object(_h, 'YOUTUBE_SUMMARISE', 'Summarise this.'), \
             patch('handler.requests') as mock_requests, \
             patch('builtins.print'):
            mock_requests.post.return_value = mock_resp
            result = _h.gemini_summarise_video('https://www.youtube.com/watch?v=abc123')
        assert result == 'Great video.'

    def test_sends_youtube_url_and_thinking_disabled(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'candidates': [{'content': {'parts': [{'text': 'Summary.'}]}}]}
        with patch.object(_h, 'GEMINI_API_KEY', 'test-key'), \
             patch.object(_h, 'YOUTUBE_SUMMARISE', 'Summarise this.'), \
             patch('handler.requests') as mock_requests:
            mock_requests.post.return_value = mock_resp
            _h.gemini_summarise_video('https://www.youtube.com/watch?v=abc123')
        _, kwargs = mock_requests.post.call_args
        body = kwargs['json']
        file_uri = body['contents'][0]['parts'][0]['fileData']['fileUri']
        assert file_uri == 'https://www.youtube.com/watch?v=abc123'
        assert body['generationConfig']['thinkingConfig']['thinkingBudget'] == 0

    def test_returns_empty_when_no_api_key(self):
        with patch.object(_h, 'GEMINI_API_KEY', ''), \
             patch.object(_h, 'YOUTUBE_SUMMARISE', 'Summarise this.'):
            result = _h.gemini_summarise_video('https://www.youtube.com/watch?v=abc123')
        assert result == ''

    def test_returns_empty_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception('HTTP 500')
        with patch.object(_h, 'GEMINI_API_KEY', 'test-key'), \
             patch.object(_h, 'YOUTUBE_SUMMARISE', 'Summarise this.'), \
             patch('handler.requests') as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = _h.gemini_summarise_video('https://www.youtube.com/watch?v=abc123')
        assert result == ''
