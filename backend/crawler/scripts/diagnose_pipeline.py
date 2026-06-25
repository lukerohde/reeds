"""
Pipeline health report — article throughput, relevance rates, and backlog.

Prints a single-shot summary of the entire digest pipeline to help
diagnose why fewer articles than expected are landing in the daily digest.

Usage (via Makefile — preferred):
    make diagnose-pipeline
"""
import os
import boto3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

TABLE_NAME = os.environ['DYNAMODB_TABLE']
table      = boto3.resource('dynamodb').Table(TABLE_NAME)

resp  = table.scan()
items = resp['Items']
while resp.get('LastEvaluatedKey'):
    resp  = table.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
    items.extend(resp['Items'])

today = datetime.now(timezone.utc).date()


def _date(item, field):
    raw = item.get(field, '')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except Exception:
        try:
            return datetime.strptime(raw[:10], '%Y-%m-%d').date()
        except Exception:
            return None


print(f'\n{"=" * 60}')
print(f'  REEDS PIPELINE HEALTH REPORT')
print(f'  Table: {TABLE_NAME}  |  {len(items)} total articles')
print(f'{"=" * 60}')

# ── Articles scraped in the last 7 days (by day) ─────────────────────────────
cutoff_7d = today - timedelta(days=7)
recent = [i for i in items if (_date(i, 'fetched_date') or _date(i, 'published_date') or today) >= cutoff_7d]

by_fetch_day = defaultdict(list)
for i in recent:
    d = _date(i, 'fetched_date') or _date(i, 'published_date')
    if d:
        by_fetch_day[d.isoformat()].append(i)

print(f'\n── Scraped last 7 days: {len(recent)} articles ──')
for day in sorted(by_fetch_day.keys(), reverse=True):
    day_items = by_fetch_day[day]
    succeeded = sum(1 for i in day_items if int(i.get('word_count', 0) or 0) > 0)
    failed    = len(day_items) - succeeded
    print(f'  {day}: {len(day_items):3d} total  ({succeeded} with content, {failed} empty/failed)')

# ── Status breakdown ──────────────────────────────────────────────────────────
status_counts = Counter(i.get('status', '') or 'unprocessed' for i in items)
print(f'\n── Status breakdown (all articles) ──')
for status in ['relevant', 'ignored', 'unprocessed']:
    print(f'  {status:14s}: {status_counts.get(status, 0)}')

# ── Relevance score distribution ──────────────────────────────────────────────
scored = [i for i in items if i.get('relevance_score') is not None]
if scored:
    score_dist = Counter(int(i['relevance_score']) for i in scored)
    print(f'\n── Relevance score distribution ({len(scored)} scored) ──')
    for s in range(1, 6):
        bar = '#' * score_dist.get(s, 0)
        print(f'  {s}: {score_dist.get(s, 0):3d}  {bar}')
else:
    print(f'\n── Relevance scores: none yet (legacy articles have no score) ──')

# ── Unserved backlog ──────────────────────────────────────────────────────────
unserved = [i for i in items if i.get('served_date', '') == '']
unserved_relevant = [i for i in unserved if i.get('status') == 'relevant']
unserved_ignored  = [i for i in unserved if i.get('status') == 'ignored']
unserved_pending  = [i for i in unserved if not i.get('status')]

print(f'\n── Unserved backlog ──')
print(f'  Total unserved:   {len(unserved)}')
print(f'    relevant:       {len(unserved_relevant)}')
print(f'    ignored:        {len(unserved_ignored)}')
print(f'    unprocessed:    {len(unserved_pending)}')

if unserved_relevant:
    print(f'\n  Relevant unserved articles:')
    unserved_relevant.sort(key=lambda x: x.get('published_date', ''), reverse=True)
    for a in unserved_relevant[:30]:
        print(f'    {a.get("published_date", "?")[:10]}  {a.get("author", "?"):20s}  {a.get("title", "?")[:60]}')

# ── Served per day (last 14 days) ─────────────────────────────────────────────
served = [i for i in items if i.get('served_date', '') != '']
served_by_day = defaultdict(list)
for i in served:
    served_by_day[i['served_date']].append(i)

cutoff_14d = (today - timedelta(days=14)).isoformat()
recent_days = sorted([d for d in served_by_day if d >= cutoff_14d], reverse=True)

print(f'\n── Served per day (last 14 days) ──')
if recent_days:
    for day in recent_days:
        day_items = served_by_day[day]
        authors = Counter(i.get('author', '?') for i in day_items)
        top = ', '.join(f'{a}({n})' for a, n in authors.most_common(3))
        print(f'  {day}: {len(day_items):2d} articles  [{top}]')
else:
    print('  (no articles served in the last 14 days)')

# ── YouTube breakdown ─────────────────────────────────────────────────────────
yt = [i for i in items if i.get('source') == 'youtube']
yt_status = Counter(i.get('status', '') or 'unprocessed' for i in yt)
yt_served = [i for i in yt if i.get('served_date', '') != '']
yt_unserved = [i for i in yt if i.get('served_date', '') == '']
yt_no_content = [i for i in yt if not i.get('content')]

print(f'\n── YouTube ──')
print(f'  Total YouTube items: {len(yt)}')
for status in ['relevant', 'ignored', 'unprocessed']:
    print(f'    {status:14s}: {yt_status.get(status, 0)}')
print(f'  Served:             {len(yt_served)}')
print(f'  Unserved:           {len(yt_unserved)}')
print(f'  No transcript:      {len(yt_no_content)}')

# ── Author diversity check ────────────────────────────────────────────────────
author_counts = Counter(i.get('author', '?') for i in unserved)
print(f'\n── Unserved by author (top 10) ──')
for author, count in author_counts.most_common(10):
    relevant = sum(1 for i in unserved if i.get('author') == author and i.get('status') == 'relevant')
    print(f'  {author:25s}: {count:3d} total ({relevant} relevant)')

print(f'\n{"=" * 60}\n')
