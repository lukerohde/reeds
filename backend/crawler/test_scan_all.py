"""
Unit tests for the shared scan_all DynamoDB helper — no real DynamoDB needed.

Run via:
    make test   (included in the crawler suite)
"""
from scripts.ddb_utils import scan_all


class FakeTable:
    """Mimics a DynamoDB table whose scan() returns results one 1MB page at a time.

    pages maps the ExclusiveStartKey value → (items, has_more). The first scan
    has no ExclusiveStartKey, so it reads page 0.
    """

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        idx = kwargs.get('ExclusiveStartKey', 0)
        items, has_more = self.pages[idx]
        resp = {'Items': list(items)}
        if has_more:
            resp['LastEvaluatedKey'] = idx + 1
        return resp


def _paginated():
    return FakeTable({
        0: ([{'url': 'a'}, {'url': 'b'}], True),
        1: ([{'url': 'c'}], False),
    })


def test_collects_every_page():
    items = scan_all(_paginated())
    assert [i['url'] for i in items] == ['a', 'b', 'c']


def test_naive_single_scan_misses_later_pages():
    # Documents the bug scan_all fixes: reading only the first page drops rows.
    table = _paginated()
    first_page_only = table.scan()['Items']  # the old buggy pattern
    assert [i['url'] for i in first_page_only] == ['a', 'b']  # 'c' silently lost


def test_threads_exclusive_start_key():
    table = _paginated()
    scan_all(table)
    assert table.calls[1].get('ExclusiveStartKey') == 1


def test_passes_through_filter_kwargs():
    table = FakeTable({0: ([], False)})
    scan_all(table, FilterExpression='source = youtube')
    assert table.calls[0]['FilterExpression'] == 'source = youtube'


def test_single_page_makes_one_call():
    table = FakeTable({0: ([{'url': 'a'}], False)})
    assert len(scan_all(table)) == 1
    assert len(table.calls) == 1


def test_empty_table():
    assert scan_all(FakeTable({0: ([], False)})) == []
