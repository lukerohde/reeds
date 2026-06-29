"""
Clear status + summary for YouTube items with no transcript so the digest
will reprocess them — picking up the Gemini fallback on the next run.

Only touches items where source='youtube' AND content='' (no transcript fetched).
Safe to re-run: items already cleared are simply found with empty status and skipped.

Usage:
    python reset_youtube_nosummary.py
"""

import os
import boto3
from boto3.dynamodb.conditions import Attr

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])

items = []
scan_kwargs = {'FilterExpression': Attr('source').eq('youtube')}
while True:
    resp = table.scan(**scan_kwargs)
    items.extend(resp.get('Items', []))
    if 'LastEvaluatedKey' not in resp:
        break
    scan_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']

reset = 0
for item in items:
    if not item.get('status'):
        continue
    # Reset items with no transcript (Gemini path) OR that have no meaningful detail yet
    # (detail='' means old code set it empty; treat same as missing)
    needs_reset = not item.get('content') or not item.get('detail')
    if not needs_reset:
        continue
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='REMOVE #s, summary SET served_date = :e',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':e': ''},
    )
    reason = 'no transcript' if not item.get('content') else 'no detail'
    print(f"  reset ({reason}): {item.get('author', '?')}: {item.get('title', item['url'])}")
    reset += 1

print(f"\nReset {reset} of {len(items)} YouTube items.")
