import os
import yaml
import boto3
import anthropic
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from boto3.dynamodb.conditions import Attr

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
except ImportError:
    google_genai = None
    genai_types  = None

_cfg = yaml.safe_load((Path(__file__).parent / 'config.yaml').read_text())

CANDIDATES_POOL  = _cfg['settings']['candidates_pool']
MAX_PER_AUTHOR   = _cfg['settings'].get('max_per_author', 0)
DIGEST_SIZE      = _cfg['settings']['digest_size']
WORDS_PER_MINUTE = _cfg['settings']['words_per_minute']

RELEVANCE_CHECK   = _cfg['prompts']['relevance_check']
SUMMARISE         = _cfg['prompts']['summarise']
CURATE            = _cfg['prompts']['curate']
YOUTUBE_SUMMARISE = _cfg['prompts'].get('youtube_summarise', '')

GEMINI_MODEL  = 'gemini-2.0-flash'
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

TABLE_NAME      = os.environ['DYNAMODB_TABLE']
BUCKET_NAME     = os.environ['BUCKET_NAME']
CF_DIST_ID      = os.environ.get('CF_DISTRIBUTION_ID', '')
DRY_RUN         = os.environ.get('DIGEST_DRY_RUN', '') == '1'

MELBOURNE = ZoneInfo('Australia/Melbourne')

dynamodb = boto3.resource('dynamodb')
table    = dynamodb.Table(TABLE_NAME)
s3       = boto3.client('s3')
cf       = boto3.client('cloudfront')
ai       = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

TEMPLATE = (Path(__file__).parent / 'template.html').read_text()


# ── Candidate selection ───────────────────────────────────────────────────────

def select_candidates(unserved, pool_size=None, max_per_author=None):
    """Return up to pool_size articles, capped at max_per_author each.

    Defaults to the module-level config values. Override params are exposed for
    testing without config dependency.
    """
    _pool = CANDIDATES_POOL if pool_size is None else pool_size
    _cap  = MAX_PER_AUTHOR  if max_per_author is None else max_per_author
    if not _cap:
        return unserved[:_pool]
    counts = {}
    pool = []
    for item in unserved:  # sorted by published_date desc
        author = item.get('author', '')
        if counts.get(author, 0) < _cap:
            pool.append(item)
            counts[author] = counts.get(author, 0) + 1
        if len(pool) >= _pool:
            break
    return pool


# ── Transform ─────────────────────────────────────────────────────────────────

def is_relevant(title, content):
    msg = ai.messages.create(
        model='claude-haiku-4-5',
        max_tokens=5,
        messages=[{'role': 'user', 'content': RELEVANCE_CHECK.format(title=title, preview=content[:500])}],
    )
    return msg.content[0].text.strip().lower().startswith('y')


def make_summary(title, author, content):
    msg = ai.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=200,
        messages=[{'role': 'user', 'content': SUMMARISE.format(title=title, author=author, text=content)}],
    )
    return msg.content[0].text


def make_youtube_summary(title, author, url):
    # TODO: set GOOGLE_API_KEY in Lambda env vars (see infra/pulumi/__main__.py)
    if not GOOGLE_API_KEY:
        return '(YouTube summary unavailable — GOOGLE_API_KEY not set)'
    if google_genai is None:
        return '(YouTube summary unavailable — google-genai package not installed)'
    client   = google_genai.Client(api_key=GOOGLE_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            genai_types.Part.from_uri(file_uri=url, mime_type='video/youtube'),
            YOUTUBE_SUMMARISE.format(title=title, author=author),
        ],
    )
    return response.text


def transform(items):
    """Relevance-check and summarise unprocessed articles. Updates DDB in place."""
    for item in items:
        if item.get('status'):
            continue

        if item.get('source') == 'youtube':
            # YouTube channels are pre-approved by config; Gemini summarises from URL
            summary = make_youtube_summary(item['title'], item['author'], item['url'])
            item['status']  = 'relevant'
            item['summary'] = summary
            table.update_item(
                Key={'url': item['url']},
                UpdateExpression='SET #s = :s, summary = :m',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'relevant', ':m': summary},
            )
            print(f"  [youtube]  {item['author']}: {item['title']}")
            continue

        content = item.get('content', '')
        if not is_relevant(item['title'], content):
            item['status'] = 'ignored'
            table.update_item(
                Key={'url': item['url']},
                UpdateExpression='SET #s = :s',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'ignored'},
            )
            print(f"  [ignored]  {item['author']}: {item['title']}")
            continue

        summary = make_summary(item['title'], item['author'], content) if content else ''
        item['status']  = 'relevant'
        item['summary'] = summary
        table.update_item(
            Key={'url': item['url']},
            UpdateExpression='SET #s = :s, summary = :m',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'relevant', ':m': summary},
        )
        print(f"  [relevant] {item['author']}: {item['title']}")


def curate(candidates):
    """Pick the most fascinating articles using AI."""
    if len(candidates) <= DIGEST_SIZE:
        return candidates

    listing = '\n\n'.join(
        f"URL: {a['url']}\nTitle: {a['title']}\nAuthor: {a['author']}\nSummary: {a.get('summary', '(no summary)')}"
        for a in candidates
    )
    msg = ai.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=500,
        messages=[{'role': 'user', 'content': CURATE.format(n=DIGEST_SIZE, articles=listing)}],
    )
    selected_urls = {
        line.strip()
        for line in msg.content[0].text.strip().splitlines()
        if line.strip().startswith('http')
    }
    selected = [a for a in candidates if a['url'] in selected_urls]
    return selected if len(selected) >= 2 else candidates[:DIGEST_SIZE]


# ── Load ──────────────────────────────────────────────────────────────────────

def read_time_label(word_count):
    if not word_count:
        return ''
    minutes = round(int(word_count) / WORDS_PER_MINUTE)
    return '< 1 min read' if minutes == 0 else f'{minutes} min read'


def prev_digest_date(current_date_str):
    try:
        resp = table.scan(
            FilterExpression=Attr('served_date').ne('') & Attr('served_date').ne(current_date_str),
            ProjectionExpression='served_date',
        )
        dates = {item['served_date'] for item in resp.get('Items', [])}
        return max(dates) if dates else None
    except Exception:
        return None


def build_html(articles, date_str, prev_date_str):
    items_html = ''
    for a in articles:
        read_time  = read_time_label(a.get('word_count', 0))
        meta_parts = [a['author'], a.get('published_date', '')[:10]]
        if read_time:
            meta_parts.append(read_time)
        meta = ' · '.join(p for p in meta_parts if p)
        items_html += f"""
    <article>
      <h2><a href="{a['url']}">{a['title']}</a></h2>
      <p class="meta">{meta}</p>
      <p class="summary">{a.get('summary', '')}</p>
    </article>"""

    prev_link = (
        f'<a href="/digest/{prev_date_str}/">← previous</a><span class="footer-sep">·</span>'
        if prev_date_str else ''
    )

    return (TEMPLATE
            .replace('%HEADING%', f'Daily Digest — {date_str}')
            .replace('%ITEMS%', items_html)
            .replace('%PREV_LINK%', prev_link))


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    today    = datetime.now(MELBOURNE)
    date_str = today.strftime('%Y-%m-%d')

    # All unserved articles (any status)
    unserved = table.scan(FilterExpression=Attr('served_date').eq(''))['Items']
    unserved.sort(key=lambda x: x.get('published_date', ''), reverse=True)
    print(f"[digest] {len(unserved)} unserved articles")

    # Transform: process unprocessed articles in the candidate pool
    candidates_raw = select_candidates(unserved)
    transform(candidates_raw)

    # Curate from relevant candidates
    relevant = [i for i in candidates_raw if i.get('status') == 'relevant']
    print(f"[digest] {len(relevant)} relevant in pool → curating to {DIGEST_SIZE}")
    articles = curate(relevant)
    print(f"[digest] selected {len(articles)} articles")
    for a in articles:
        print(f"  {a['author']}: {a['title']}")

    if not articles:
        return {'message': 'No articles to serve'}

    prev_date_str = None if DRY_RUN else prev_digest_date(date_str)
    html = build_html(articles, date_str, prev_date_str)

    if DRY_RUN:
        preview_path = '/tmp/reeds-digest-preview.html'
        with open(preview_path, 'w') as f:
            f.write(html)
        print(f"[digest] DRY RUN — written to {preview_path}")
    else:
        for key in [f'digest/{date_str}/index.html', 'digest/latest/index.html']:
            s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=html.encode(), ContentType='text/html')
            print(f"[digest] uploaded → s3://{BUCKET_NAME}/{key}")
        if CF_DIST_ID:
            try:
                cf.create_invalidation(
                    DistributionId=CF_DIST_ID,
                    InvalidationBatch={
                        'Paths': {'Quantity': 2, 'Items': ['/digest/latest/*', f'/digest/{date_str}/*']},
                        'CallerReference': date_str,
                    },
                )
                print(f"[digest] CloudFront invalidation created for {CF_DIST_ID}")
            except Exception as e:
                print(f"[digest] CF invalidation failed (non-fatal): {e}")
        for a in articles:
            table.update_item(
                Key={'url': a['url']},
                UpdateExpression='SET served_date = :d',
                ExpressionAttributeValues={':d': date_str},
            )

    return {
        'served': len(articles),
        'date':   date_str,
        'urls':   [a['url'] for a in articles],
    }
