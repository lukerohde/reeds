"""
Reset today's served articles back to unserved so the digest can be re-run.

Usage:
    python reset_today.py
"""

import os
import boto3
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Attr

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

items = table.scan(FilterExpression=Attr('served_date').eq(today))['Items']
for item in items:
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='SET served_date = :e',
        ExpressionAttributeValues={':e': ''},
    )

print(f"Reset {len(items)} articles served on {today}")
