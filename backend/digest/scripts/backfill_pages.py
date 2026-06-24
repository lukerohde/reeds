"""
Re-render all historical digest pages with the current template.

Scans DynamoDB for every served article, groups by served_date, and re-uploads
each day's HTML to S3 using build_html() — picking up the latest template
(infinite scroll sentinel, read-more sections, any other improvements) without
re-running AI summarisation.

Also re-uploads digest/latest/ pointing to the most recent date.
One CloudFront invalidation for /digest/* covers everything.

Usage:
    make backfill-scroll
"""
import os
import time
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Attr

from handler import build_html, s3, BUCKET_NAME, CF_DIST_ID

DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
table = boto3.resource('dynamodb').Table(DYNAMODB_TABLE)


def scan_all_served():
    resp = table.scan(FilterExpression=Attr('served_date').ne(''))
    items = resp['Items']
    while 'LastEvaluatedKey' in resp:
        resp = table.scan(
            FilterExpression=Attr('served_date').ne(''),
            ExclusiveStartKey=resp['LastEvaluatedKey'],
        )
        items.extend(resp['Items'])
    return items


def main():
    print('[backfill] scanning served articles…')
    items = scan_all_served()

    by_date = defaultdict(list)
    for item in items:
        by_date[item['served_date']].append(item)

    dates = sorted(by_date.keys())
    print(f'[backfill] {len(dates)} dates, {len(items)} articles')

    for i, date_str in enumerate(dates):
        articles = by_date[date_str]
        prev_date_str = dates[i - 1] if i > 0 else None
        html = build_html(articles, date_str, prev_date_str)
        key = f'digest/{date_str}/index.html'
        s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=html.encode(), ContentType='text/html')
        print(f'  {key}  ({len(articles)} articles, prev={prev_date_str})')

    if dates:
        latest = dates[-1]
        prev_latest = dates[-2] if len(dates) > 1 else None
        html = build_html(by_date[latest], latest, prev_latest)
        s3.put_object(Bucket=BUCKET_NAME, Key='digest/latest/index.html', Body=html.encode(), ContentType='text/html')
        print(f'  digest/latest/index.html → {latest}')

    if CF_DIST_ID:
        cf = boto3.client('cloudfront')
        cf.create_invalidation(
            DistributionId=CF_DIST_ID,
            InvalidationBatch={
                'Paths': {'Quantity': 1, 'Items': ['/digest/*']},
                'CallerReference': str(int(time.time())),
            },
        )
        print(f'[backfill] CloudFront invalidation created for {CF_DIST_ID}')

    print('[backfill] done.')


if __name__ == '__main__':
    main()
