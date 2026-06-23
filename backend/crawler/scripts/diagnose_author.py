"""
Show DynamoDB article statistics for a specific author.

Outputs: total/served/unserved counts, status breakdown, position in the
unserved queue (the candidates pool), and per-day served history.

Usage (via Makefile — preferred):
    make diagnose-author AUTHOR="Simon Willison"

Direct usage:
    AUTHOR="Simon Willison" python diagnose_author.py
"""
import os
import sys
import boto3
from collections import Counter, defaultdict

AUTHOR = os.environ.get('AUTHOR', '').strip()
if not AUTHOR:
    print('Usage: make diagnose-author AUTHOR="Author Name"')
    sys.exit(1)

TABLE_NAME = os.environ['DYNAMODB_TABLE']
table      = boto3.resource('dynamodb').Table(TABLE_NAME)

# Full scan (table is small enough that a full scan is fine here)
resp  = table.scan()
items = resp['Items']
while resp.get('LastEvaluatedKey'):
    resp  = table.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
    items.extend(resp['Items'])

total        = len(items)
author_items = [i for i in items if i.get('author', '') == AUTHOR]

if not author_items:
    print(f'\nNo articles found for author: {AUTHOR!r}')
    print(f'Total articles in table: {total}')
    print(f'Known authors: {sorted({i.get("author", "?") for i in items})}')
    sys.exit(0)

unserved         = [i for i in author_items if i.get('served_date', '') == '']
served           = [i for i in author_items if i.get('served_date', '') != '']
status_breakdown = Counter(i.get('status', 'unprocessed') for i in unserved)

print(f'\n=== {AUTHOR} ===')
print(f'Total articles:   {len(author_items)}')
print(f'  served:         {len(served)}')
print(f'  unserved:       {len(unserved)}')
print(f'  unserved status breakdown: {dict(status_breakdown)}')

# Position in the full unserved queue (sorted by recency, candidates_pool is top 20)
all_unserved = [i for i in items if i.get('served_date', '') == '']
all_unserved.sort(key=lambda x: x.get('published_date', ''), reverse=True)

positions = [i + 1 for i, a in enumerate(all_unserved) if a.get('author') == AUTHOR]
top20_count = sum(1 for a in all_unserved[:20] if a.get('author') == AUTHOR)

print(f'\nUnserved queue (sorted by date desc):')
print(f'  Total unserved (all authors): {len(all_unserved)}')
print(f'  {AUTHOR} in top-20 (candidates pool): {top20_count}/20')
if positions:
    print(f'  Positions: {positions[:30]}{"..." if len(positions) > 30 else ""}')

# Per-day breakdown for served articles (last 10 days that had this author)
by_day = defaultdict(list)
for i in served:
    by_day[i['served_date']].append(i)

if by_day:
    all_served  = [i for i in items if i.get('served_date', '') != '']
    day_totals  = Counter(i['served_date'] for i in all_served)
    print(f'\nServed per day (last 10 days with {AUTHOR}):')
    for day in sorted(by_day.keys(), reverse=True)[:10]:
        print(f'  {day}: {AUTHOR}={len(by_day[day])}/{day_totals[day]} total')
