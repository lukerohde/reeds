"""
Print all processed (status=relevant) unserved articles with their summaries.
Useful for inspecting what the digest generated before curation.

Usage:
    make show-candidates
"""
import os
import boto3
from boto3.dynamodb.conditions import Attr
from ddb_utils import scan_all

endpoint = os.environ.get('AWS_ENDPOINT_URL')
ddb      = boto3.resource('dynamodb', endpoint_url=endpoint)
table    = ddb.Table(os.environ['DYNAMODB_TABLE'])

all_items = scan_all(table, FilterExpression=Attr('served_date').eq(''))
pending   = [i for i in all_items if not i.get('status')]
items     = sorted(
    [i for i in all_items if i.get('status') == 'relevant'],
    key=lambda x: x.get('published_date', ''), reverse=True,
)

if pending:
    print(f'⏳  {len(pending)} article(s) not yet processed — run `make dev` to transform them.\n')

if not items:
    print('No relevant unserved articles found.')
    raise SystemExit(0)

blogs   = [i for i in items if i.get('source') != 'youtube']
youtube = [i for i in items if i.get('source') == 'youtube']

def show(item):
    wc  = item.get('word_count', 0)
    src = '[yt]' if item.get('source') == 'youtube' else '    '
    score = item.get('relevance_score', '?')
    print(f"{src} [{score}] {item['author']}: {item['title']}")
    print(f"     {item['url']}")
    print(f"     {item.get('published_date', '')[:10]}  {wc} words")
    summary = item.get('summary', '').strip()
    if summary:
        for line in summary.splitlines():
            print(f"     {line}")
    else:
        print('     (no summary)')
    print()

if youtube:
    print(f'── YouTube ({len(youtube)}) ──────────────────────────────────────────')
    for item in youtube:
        show(item)

print(f'── Blog articles ({len(blogs)}) ────────────────────────────────────────')
for item in blogs:
    show(item)

print(f'Total relevant: {len(items)}')
