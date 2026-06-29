"""
Clear AI-generated fields (status, summary, served_date) from all local articles
without deleting content. Use this between prompt-engineering iterations so you
can re-run the digest without re-crawling.

Usage:
    python local_soft_reset.py
"""

import os
import boto3
from ddb_utils import scan_all

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
items = scan_all(table)
for item in items:
    table.update_item(
        Key={'url': item['url']},
        UpdateExpression='REMOVE #s, summary, relevance_score SET served_date = :e',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':e': ''},
    )
print(f"Soft-reset {len(items)} articles (status/summary cleared, content kept)")
