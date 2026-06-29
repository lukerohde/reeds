import os
import re
import json
from html import escape as html_escape
import yaml
import boto3
import requests
import anthropic
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from boto3.dynamodb.conditions import Attr

_cfg = yaml.safe_load((Path(__file__).parent / 'config.yaml').read_text())

DISCOVERY_POOL           = _cfg['settings'].get('discovery_pool', _cfg['settings'].get('candidates_pool', 30))
CURATION_POOL            = _cfg['settings'].get('curation_pool', 30)
MAX_PER_AUTHOR           = _cfg['settings'].get('max_per_author', 0)
DIGEST_SIZE              = _cfg['settings']['digest_size']
WORDS_PER_MINUTE         = _cfg['settings']['words_per_minute']
SUMMARISE_LONG_THRESHOLD = _cfg['settings'].get('summarise_long_threshold', 500)

RELEVANCE_CHECK       = _cfg['prompts']['relevance_check']
SUMMARISE_SHORT       = _cfg['prompts'].get('summarise_short', '')
SUMMARISE_LONG        = _cfg['prompts'].get('summarise_long', SUMMARISE_SHORT)
YOUTUBE_SUMMARISE = _cfg['prompts'].get('youtube_summarise', '')
CURATE                = _cfg['prompts']['curate']

TABLE_NAME      = os.environ['DYNAMODB_TABLE']
BUCKET_NAME     = os.environ['BUCKET_NAME']
CF_DIST_ID      = os.environ.get('CF_DISTRIBUTION_ID', '')
DRY_RUN         = os.environ.get('DIGEST_DRY_RUN', '') == '1'

# Gemini summarises YouTube videos directly from their URL when no transcript is
# available (youtube_transcript_api is IP-blocked from cloud hosts like Lambda).
# It's an authenticated Google API call, so it isn't subject to that block.
GEMINI_API_KEY  = os.environ.get('GOOGLE_API_KEY', '')
GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'

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

    Used for discovery: picks which unprocessed articles to AI-score this run.
    """
    _pool = DISCOVERY_POOL if pool_size is None else pool_size
    _cap  = MAX_PER_AUTHOR if max_per_author is None else max_per_author
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


def build_curation_pool(eligible, pool_size=None, max_per_author=None):
    """Build a priority-sorted pool of relevant articles for curation.

    Pulls from ALL relevant unserved articles (not just today's discovery batch),
    sorted by relevance_score DESC then published_date DESC. Applies per-author
    cap and truncates to pool_size.
    """
    _pool = CURATION_POOL  if pool_size is None else pool_size
    _cap  = MAX_PER_AUTHOR if max_per_author is None else max_per_author

    relevant = [i for i in eligible if i.get('status') == 'relevant']
    relevant.sort(key=lambda x: (int(x.get('relevance_score', 3)),
                                 x.get('published_date', '')),
                  reverse=True)

    if not _cap:
        return relevant[:_pool]
    counts = {}
    pool = []
    for item in relevant:
        author = item.get('author', '')
        if counts.get(author, 0) < _cap:
            pool.append(item)
            counts[author] = counts.get(author, 0) + 1
        if len(pool) >= _pool:
            break
    return pool


# ── Transform ─────────────────────────────────────────────────────────────────

def score_relevance(title, content):
    """Return a 1-5 relevance score (0 if unparseable)."""
    msg = ai.messages.create(
        model='claude-haiku-4-5',
        max_tokens=5,
        messages=[{'role': 'user', 'content': RELEVANCE_CHECK.format(title=title, preview=content[:500])}],
    )
    text = msg.content[0].text.strip()
    try:
        return max(1, min(5, int(text[0])))
    except (ValueError, IndexError):
        if text.lower().startswith('y'):
            return 3
        return 0


def make_summary(title, author, content, word_count=0):
    """Summarise content via Claude. Short articles get verbatim excerpt; long ones get TLDR."""
    wc     = int(word_count or 0)
    prompt = SUMMARISE_LONG if wc >= SUMMARISE_LONG_THRESHOLD else SUMMARISE_SHORT
    msg    = ai.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=200,
        messages=[{'role': 'user', 'content': prompt.format(title=title, author=author, text=content)}],
    )
    return msg.content[0].text


def make_youtube_summary(title, author, content):
    """Summarise a YouTube transcript via Claude, returning (summary, detail) tuple.

    Uses the shared youtube_summarise prompt — same instructions as Gemini, just
    fed a text transcript instead of a video URL. Falls back to plain summary if
    the prompt is not configured or JSON parsing fails.
    """
    if not YOUTUBE_SUMMARISE:
        return make_summary(title, author, content), ''
    msg = ai.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=2500,
        messages=[{'role': 'user', 'content': YOUTUBE_SUMMARISE.format(
            title=title, author=author, text=content,
        )}],
    )
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', msg.content[0].text.strip())
    try:
        data    = json.loads(text)
        summary = data.get('summary') or ''
        detail  = data.get('detail') or ''
        if isinstance(detail, str) and detail.strip().lower() in ('null', 'none'):
            detail = ''
        return summary, detail
    except Exception as e:
        print(f"  [claude] JSON parse failed for {title}: {e}")
        return msg.content[0].text, ''


def gemini_summarise_video(url):
    """Summarise a YouTube video directly from its URL via Gemini.

    Used when no transcript could be fetched (captions disabled, age-restricted,
    or the IP block on cloud hosts). Returns (summary, detail) tuple; both are
    empty strings if Gemini is not configured or fails. detail is non-empty only
    for information-dense videos where a full write-up adds value.
    """
    if not GEMINI_API_KEY or not YOUTUBE_SUMMARISE:
        return '', ''
    try:
        body = {
            'contents': [{'parts': [
                {'fileData': {'fileUri': url}},
                {'text': YOUTUBE_SUMMARISE.format(title='', author='', text='')},
            ]}],
            'generationConfig': {'thinkingConfig': {'thinkingBudget': 0}},
        }
        r = requests.post(
            GEMINI_ENDPOINT, json=body,
            headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_API_KEY},
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()['candidates'][0]['content']['parts'][0]['text']
        # Strip markdown code fences Gemini sometimes wraps around JSON
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
        data = json.loads(text)
        summary = data.get('summary') or ''
        detail  = data.get('detail') or ''
        if isinstance(detail, str) and detail.strip().lower() in ('null', 'none'):
            detail = ''
        label = ' (+detail)' if detail else ''
        print(f"  [gemini] summarised {url}{label}")
        return summary, detail
    except Exception as e:
        print(f"  [gemini] failed for {url}: {type(e).__name__}: {e}")
        return '', ''


def transform(items):
    """Relevance-check and summarise unprocessed items. Updates DDB in place.

    Blogs and YouTube transcripts share one path: their `content` text drives
    the relevance check and the summary. A YouTube item with no transcript is the
    one exception — Gemini summarises it straight from the video URL, and that
    output doubles as both the relevance signal and the stored summary (no second
    Claude call). If Gemini is unavailable the video is relevance-checked on its
    title and served without a summary; the crawler keeps retrying its transcript.
    """
    for item in items:
        if item.get('status'):
            continue

        content       = item.get('content', '')
        ready_summary = None  # set when content IS already a summary (Gemini path)
        ready_detail  = None

        if item.get('source') == 'youtube' and not content:
            ready_summary, ready_detail = gemini_summarise_video(item['url'])
            content = ready_summary  # use as the relevance signal

        score = score_relevance(item['title'], content)
        if score <= 1:
            item['status'] = 'ignored'
            item['relevance_score'] = score
            table.update_item(
                Key={'url': item['url']},
                UpdateExpression='SET #s = :s, relevance_score = :rs',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'ignored', ':rs': score},
            )
            print(f"  [ignored:{score}]  {item['author']}: {item['title']}")
            continue

        if item.get('source') == 'youtube' and content and ready_summary is None:
            ready_summary, ready_detail = make_youtube_summary(item['title'], item['author'], content)

        summary = ready_summary if ready_summary is not None else (
            make_summary(item['title'], item['author'], content, word_count=item.get('word_count', 0)) if content else ''
        )
        detail = ready_detail if ready_detail is not None else ''
        item['status']  = 'relevant'
        item['summary'] = summary
        item['detail']  = detail
        item['relevance_score'] = score
        table.update_item(
            Key={'url': item['url']},
            UpdateExpression='SET #s = :s, summary = :m, detail = :d, relevance_score = :rs',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'relevant', ':m': summary, ':d': detail, ':rs': score},
        )
        print(f"  [relevant:{score}] {item['author']}: {item['title']}")


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
        dates = set()
        scan_kwargs = {
            'FilterExpression': Attr('served_date').ne('') & Attr('served_date').ne(current_date_str),
            'ProjectionExpression': 'served_date',
        }
        while True:
            resp = table.scan(**scan_kwargs)
            dates.update(item['served_date'] for item in resp.get('Items', []))
            if 'LastEvaluatedKey' not in resp:
                break
            scan_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        return max(dates) if dates else None
    except Exception:
        return None


def _md_to_html(text):
    """Convert bold/italic markdown to HTML so summaries render correctly.

    Escapes HTML first so literal angle brackets in LLM-generated text (e.g. a
    summary discussing example tags like <table> or <canvas>) render as text
    instead of being parsed as real elements and corrupting the page structure.
    """
    text = html_escape(text, quote=False)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', text)
    return text


def _detail_to_html(text):
    """Convert Gemini detail prose (paragraphs + bold/italic) to HTML paragraphs."""
    paragraphs = re.split(r'\n\n+', text.strip())
    return ''.join(
        f'<p>{_md_to_html(p.strip())}</p>'
        for p in paragraphs if p.strip()
    )


def build_html(articles, date_str, prev_date_str):
    items_html = ''
    for a in articles:
        read_time  = read_time_label(a.get('word_count', 0))
        meta_parts = [html_escape(a['author']), a.get('published_date', '')[:10]]
        if read_time:
            meta_parts.append(read_time)
        meta = ' · '.join(p for p in meta_parts if p)
        detail     = a.get('detail', '')
        detail_html = ''
        if detail:
            detail_html = f"""
      <details class="detail">
        <summary>read more</summary>
        <div class="detail-body">{_detail_to_html(detail)}</div>
      </details>"""
        items_html += f"""
    <article>
      <h2><a href="{html_escape(a['url'], quote=True)}">{html_escape(a['title'])}</a></h2>
      <p class="meta">{meta}</p>
      <p class="summary">{_md_to_html(a.get('summary', ''))}</p>{detail_html}
    </article>"""

    prev_link = (
        f'<a href="/digest/{prev_date_str}/">← previous</a><span class="footer-sep">·</span>'
        if prev_date_str else ''
    )

    prev_url = f'/digest/{prev_date_str}/' if prev_date_str else ''
    return (TEMPLATE
            .replace('%HEADING%', f'Daily Digest — {date_str}')
            .replace('%ITEMS%', items_html)
            .replace('%PREV_LINK%', prev_link)
            .replace('%PREV_URL%', prev_url))


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    today    = datetime.now(MELBOURNE)
    date_str = today.strftime('%Y-%m-%d')

    # All unserved articles, excluding already-ignored ones
    unserved = []
    scan_kwargs = {'FilterExpression': Attr('served_date').eq('')}
    while True:
        resp = table.scan(**scan_kwargs)
        unserved.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        scan_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
    unserved.sort(key=lambda x: x.get('published_date', ''), reverse=True)
    eligible = [i for i in unserved if i.get('status') != 'ignored']
    print(f"[digest] {len(unserved)} unserved articles ({len(unserved) - len(eligible)} ignored, {len(eligible)} eligible)")

    # Phase 1: DISCOVERY — AI-score new/unprocessed articles
    unprocessed = [i for i in eligible if not i.get('status')]
    discovery_batch = select_candidates(unprocessed)
    transform(discovery_batch)

    # Phase 2: EDITORIAL — curate from ALL relevant unserved (old + newly discovered)
    curation_pool = build_curation_pool(eligible)
    print(f"[digest] {len(curation_pool)} in curation pool → curating to {DIGEST_SIZE}")
    articles = curate(curation_pool)
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
                        'CallerReference': today.strftime('%Y-%m-%dT%H:%M:%S'),
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
