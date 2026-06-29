"""Shared DynamoDB helpers for the maintenance scripts.

A bare ``table.scan()`` (with or without a FilterExpression) returns only the
first 1 MB page of results; any rows beyond that are silently dropped. Every
script that needs the full table must page through ``LastEvaluatedKey`` — see
commits b06bbde / 31dd7b4 where the same bug bit the digest handler.
"""


def scan_all(table, **scan_kwargs):
    """Return every item from ``table.scan(**scan_kwargs)``, across all pages."""
    items = []
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp.get('Items', []))
        key = resp.get('LastEvaluatedKey')
        if not key:
            break
        scan_kwargs['ExclusiveStartKey'] = key
    return items
