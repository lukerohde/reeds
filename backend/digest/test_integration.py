"""
Integration tests for the reeds digest handler against LocalStack.

Three test classes with increasing cost:

  TestRenderOnly       — no AI, seeds pre-summarised articles, tests curate+render
  TestCurateWithAI     — one AI call (curate), seeds pre-summarised above DIGEST_SIZE
  TestFullPipeline     — full AI pipeline (relevance + summarise + curate)

Prerequisites:
  make local-up              # start LocalStack + create table/bucket
  export ANTHROPIC_API_KEY=  # only needed for TestCurateWithAI / TestFullPipeline

Run:
  make test-integration      # runs TestRenderOnly (and AI classes if key is set)
"""

import os
import pytest
import boto3
from datetime import datetime, timezone, timedelta

ENDPOINT = os.environ.get('AWS_ENDPOINT_URL', 'http://localstack:4566')
TABLE    = os.environ.get('DYNAMODB_TABLE', 'reeds-articles')
PREVIEW  = '/tmp/reeds-digest-preview.html'
HAS_AI   = bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_table():
    """Wipe all articles before each test and remove preview file after."""
    ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
    table = ddb.Table(TABLE)
    kwargs = {
        'ProjectionExpression':    '#u',
        'ExpressionAttributeNames': {'#u': 'url'},
    }
    with table.batch_writer() as batch:
        while True:
            resp = table.scan(**kwargs)
            for item in resp['Items']:
                batch.delete_item(Key={'url': item['url']})
            if 'LastEvaluatedKey' not in resp:
                break
            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
    yield
    if os.path.exists(PREVIEW):
        os.remove(PREVIEW)


def _seed(n, *, raw=False):
    """Insert n articles.

    raw=False  — pre-summarised (status=relevant, summary set): no AI calls needed
    raw=True   — unprocessed (status='', summary=''): triggers full transform
    """
    ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
    table = ddb.Table(TABLE)
    now   = datetime.now(timezone.utc)
    with table.batch_writer() as batch:
        for i in range(n):
            item = {
                'url':            f'https://example.com/article-{i}',
                'author':         f'Author {i % 3}',
                'title':          f'Article {i}: Python, distributed systems, and observability',
                'published_date': (now - timedelta(hours=i)).isoformat(),
                'fetched_date':   now.isoformat(),
                'served_date':    '',
                'word_count':     str(400 + i * 50),
                'content':        (
                    f'Software engineering insight {i}. This article covers Python, '
                    f'AI, distributed systems, and DevOps best practices. ' * 20
                ),
            }
            if raw:
                item['status']  = ''
                item['summary'] = ''
            else:
                item['status']  = 'relevant'
                item['summary'] = f'Key insight {i}: always verify your Lambda packages are bundled.'
            batch.put_item(Item=item)


# ── TestRenderOnly — zero AI calls ────────────────────────────────────────────

class TestRenderOnly:
    """Seeds pre-summarised articles at or below DIGEST_SIZE — no AI calls."""

    def test_produces_html_file(self):
        _seed(8)
        from handler import handler
        result = handler({}, None)
        assert result['served'] == 8
        assert os.path.exists(PREVIEW)

    def test_html_structure(self):
        _seed(5)
        from handler import handler
        handler({}, None)
        html = open(PREVIEW).read()
        assert 'Daily Digest' in html
        assert html.count('<article>') == 5
        assert 'https://example.com/article-' in html
        assert '<h2>' in html
        assert 'min read' in html

    def test_returns_correct_shape(self):
        _seed(3)
        from handler import handler
        result = handler({}, None)
        assert set(result.keys()) == {'served', 'date', 'urls'}
        assert len(result['urls']) == 3
        assert result['date']  # non-empty date string

    def test_no_articles_returns_early(self):
        from handler import handler
        result = handler({}, None)
        assert result == {'message': 'No articles to serve'}
        assert not os.path.exists(PREVIEW)

    def test_already_served_articles_excluded(self):
        """Articles with a served_date should not be picked up."""
        _seed(5)
        # Manually mark two as served
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        table = ddb.Table(TABLE)
        for i in range(2):
            table.update_item(
                Key={'url': f'https://example.com/article-{i}'},
                UpdateExpression='SET served_date = :d',
                ExpressionAttributeValues={':d': '2020-01-01'},
            )
        from handler import handler
        result = handler({}, None)
        assert result['served'] == 3

    def test_dry_run_does_not_set_served_date(self):
        """DRY_RUN=1 must not update served_date in DynamoDB."""
        _seed(3)
        from handler import handler
        handler({}, None)
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        items = ddb.Table(TABLE).scan()['Items']
        assert all(item['served_date'] == '' for item in items)


# ── TestPrevDigestDate — previous link logic ──────────────────────────────────

class TestPrevDigestDate:
    """prev_digest_date must derive the previous digest date from DynamoDB served_date values."""

    def test_returns_none_when_no_prior_digests(self):
        from handler import prev_digest_date
        assert prev_digest_date('2099-01-01') is None

    def test_returns_most_recent_prior_date(self):
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        table = ddb.Table(TABLE)
        now   = datetime.now(timezone.utc)
        for i, date in enumerate(['2099-01-01', '2099-01-02', '2099-01-03']):
            table.put_item(Item={
                'url':            f'https://example.com/prev-{i}',
                'author':         'Author',
                'title':          f'Article {i}',
                'published_date': now.isoformat(),
                'fetched_date':   now.isoformat(),
                'served_date':    date,
                'word_count':     '100',
                'content':        '',
            })
        from handler import prev_digest_date
        assert prev_digest_date('2099-01-04') == '2099-01-03'

    def test_excludes_current_date(self):
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        table = ddb.Table(TABLE)
        now   = datetime.now(timezone.utc)
        for i, date in enumerate(['2099-01-01', '2099-01-02']):
            table.put_item(Item={
                'url':            f'https://example.com/excl-{i}',
                'author':         'Author',
                'title':          f'Article {i}',
                'published_date': now.isoformat(),
                'fetched_date':   now.isoformat(),
                'served_date':    date,
                'word_count':     '100',
                'content':        '',
            })
        from handler import prev_digest_date
        assert prev_digest_date('2099-01-02') == '2099-01-01'

    def test_prev_link_appears_in_html(self):
        """build_html with a prev_date_str must render a ← previous link."""
        from handler import build_html
        articles = [{
            'url':            'https://example.com/a',
            'title':          'Test Article',
            'author':         'Author',
            'published_date': '2099-01-02T00:00:00+00:00',
            'summary':        'A summary.',
        }]
        html = build_html(articles, '2099-01-02', '2099-01-01')
        assert '← previous' in html
        assert '/digest/2099-01-01/' in html

    def test_no_prev_link_when_none(self):
        """build_html with prev_date_str=None must not render a previous link."""
        from handler import build_html
        articles = [{
            'url':            'https://example.com/a',
            'title':          'Test Article',
            'author':         'Author',
            'published_date': '2099-01-02T00:00:00+00:00',
            'summary':        'A summary.',
        }]
        html = build_html(articles, '2099-01-02', None)
        assert '← previous' not in html


# ── TestCurateWithAI — one AI call ────────────────────────────────────────────

@pytest.mark.skipif(not HAS_AI, reason='ANTHROPIC_API_KEY not set')
class TestCurateWithAI:
    """Seeds pre-summarised articles above DIGEST_SIZE — triggers one AI curate call."""

    def test_curates_to_digest_size(self):
        _seed(15)
        from handler import handler
        result = handler({}, None)
        assert result['served'] <= 10
        assert result['served'] >= 2  # fallback floor in curate()

    def test_html_article_count_matches_served(self):
        _seed(15)
        from handler import handler
        result = handler({}, None)
        html  = open(PREVIEW).read()
        assert html.count('<article>') == result['served']


# ── TestFullPipeline — full AI transform ──────────────────────────────────────

@pytest.mark.skipif(not HAS_AI, reason='ANTHROPIC_API_KEY not set')
class TestFullPipeline:
    """Seeds raw articles and runs the complete relevance+summarise+curate pipeline."""

    def test_transform_produces_output(self):
        _seed(15, raw=True)
        from handler import handler
        result = handler({}, None)
        assert 'served' in result
        # Some articles should make it through relevance filter
        assert result['served'] > 0
        assert os.path.exists(PREVIEW)

    def test_status_written_to_dynamodb(self):
        """Transform must persist status on each candidate article."""
        _seed(5, raw=True)
        from handler import handler
        handler({}, None)
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        items = ddb.Table(TABLE).scan()['Items']
        for item in items:
            assert item.get('status') in ('relevant', 'ignored'), (
                f"Unexpected status '{item.get('status')}' for {item['url']}"
            )

    def test_off_topic_article_ignored(self):
        """Clearly off-topic content should be marked ignored by relevance check."""
        ddb   = boto3.resource('dynamodb', endpoint_url=ENDPOINT)
        table = ddb.Table(TABLE)
        now   = datetime.now(timezone.utc)
        table.put_item(Item={
            'url':            'https://example.com/pasta-recipe',
            'author':         'Chef',
            'title':          'My favourite summer pasta recipe',
            'published_date': now.isoformat(),
            'fetched_date':   now.isoformat(),
            'served_date':    '',
            'word_count':     '300',
            'content':        'Boil pasta for 10 minutes. Add olive oil and salt. Serve warm. ' * 20,
            'status':         '',
            'summary':        '',
        })
        from handler import handler
        handler({}, None)
        item = table.get_item(Key={'url': 'https://example.com/pasta-recipe'})['Item']
        assert item['status'] == 'ignored'
