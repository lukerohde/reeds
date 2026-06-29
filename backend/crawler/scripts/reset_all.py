"""
Delete ALL articles from the DDB table and re-crawl from scratch.
Use when the schema has changed and existing items need to be re-fetched.

Usage:
    python reset_all.py
"""

import os
import boto3
from ddb_utils import scan_all

table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
items = scan_all(table)
with table.batch_writer() as batch:
    for item in items:
        batch.delete_item(Key={'url': item['url']})
print(f"Deleted {len(items)} articles — run 'make crawl' to re-populate")
