"""
Reset today's served articles back to unserved so the digest can be re-run.

Usage:
    python reset_today.py
"""

import os
import boto3
from datetime import datetime
from zoneinfo import ZoneInfo
from boto3.dynamodb.conditions import Attr
from ddb_utils import scan_all

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
today = datetime.now(ZoneInfo('Australia/Melbourne')).strftime('%Y-%m-%d')

items = scan_all(table, FilterExpression=Attr('served_date').eq(today))
for item in items:
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='SET served_date = :e',
        ExpressionAttributeValues={':e': ''},
    )

print(f"Reset {len(items)} articles served on {today}")
