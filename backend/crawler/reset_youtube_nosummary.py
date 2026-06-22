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

response = table.scan(
    FilterExpression=Attr('source').eq('youtube') & Attr('content').eq('')
)
items = response['Items']

reset = 0
for item in items:
    if not item.get('status'):
        continue
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='REMOVE #s, summary SET served_date = :e',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':e': ''},
    )
    print(f"  reset: {item.get('author', '?')}: {item.get('title', item['url'])}")
    reset += 1

print(f"\nReset {reset} of {len(items)} YouTube items with no transcript.")
