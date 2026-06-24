"""
Clone production DynamoDB table and S3 digest pages to LocalStack.
Safe to run multiple times — existing local data is overwritten.

Usage:
    make local-clone-prod
"""
import os
import sys

import boto3

REGION       = os.environ.get('AWS_DEFAULT_REGION', 'eu-west-1')
PROD_TABLE   = os.environ['DYNAMODB_TABLE']
PROD_BUCKET  = os.environ['BUCKET_NAME']
LOCAL_TABLE  = 'reeds-articles'
LOCAL_BUCKET = 'reeds-local'
LOCAL_URL    = 'http://localstack:4566'

prod_ddb  = boto3.resource('dynamodb', region_name=REGION)
prod_s3   = boto3.client('s3', region_name=REGION)
local_ddb = boto3.resource('dynamodb', endpoint_url=LOCAL_URL,
                           region_name='eu-west-1',
                           aws_access_key_id='test',
                           aws_secret_access_key='test')
local_s3  = boto3.client('s3', endpoint_url=LOCAL_URL,
                         region_name='eu-west-1',
                         aws_access_key_id='test',
                         aws_secret_access_key='test')


def clone_dynamodb():
    src = prod_ddb.Table(PROD_TABLE)
    dst = local_ddb.Table(LOCAL_TABLE)

    items = []
    resp = src.scan()
    items.extend(resp['Items'])
    while 'LastEvaluatedKey' in resp:
        resp = src.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
        items.extend(resp['Items'])

    print(f'[clone] {len(items)} DynamoDB items → LocalStack')
    with dst.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print('[clone] DynamoDB done')


def clone_s3():
    paginator = prod_s3.get_paginator('list_objects_v2')
    count = 0
    for page in paginator.paginate(Bucket=PROD_BUCKET, Prefix='digest/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            resp = prod_s3.get_object(Bucket=PROD_BUCKET, Key=key)
            body = resp['Body'].read()
            content_type = resp.get('ContentType', 'text/html')
            local_s3.put_object(Bucket=LOCAL_BUCKET, Key=key, Body=body, ContentType=content_type)
            count += 1
            if count % 10 == 0:
                print(f'  ...{count} S3 objects copied')
    print(f'[clone] {count} S3 objects → LocalStack')


if __name__ == '__main__':
    clone_dynamodb()
    clone_s3()
    print('[clone] done.')
