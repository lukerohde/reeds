"""
Delete all articles from the local DDB table so the next local-crawl
re-fetches everything fresh (including content).

Usage:
    python local_reset.py
"""

import os
import boto3

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
items = table.scan()['Items']
with table.batch_writer() as batch:
    for item in items:
        batch.delete_item(Key={'url': item['url']})
print(f"Deleted {len(items)} articles")
