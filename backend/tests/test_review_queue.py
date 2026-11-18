"""Named queue filters and optimistic edit conflicts through the public API."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from test_invoice_list import login
from test_review_api import decision, headers, review


@pytest.mark.parametrize('query,expected', [
    ('supplier=Supplier', ('first', 'second')),
    ('supplier=Missing', ()),
    ('date_from=2026-10-03', ()),
    ('date_to=2026-10-01', ()),
    ('min_amount=25', ('second',)),
    ('max_amount=30', ('first',)),
    ('reason=price', ('first', 'second')),
    ('operational_state=ESCALATED', ()),
    ('operational_state=REVIEW_REQUIRED', ('first', 'second')),
    ('min_age_days=1', ()),
    ('sort=amount&direction=desc', ('second', 'first')),
    ('sort=amount&offset=1&limit=1', ('second',)),
])
def test_queue_filters_order_and_pagination(review, query, expected):
    client, _, seed = review
    cases = {'first': seed('FIRST', total='24'), 'second': seed('SECOND', total='36')}
    login(client, 'analyst')
    response = client.get('/review?' + query)
    assert response.status_code == 200
    body = response.json()
    received = [item['invoice_id'] for item in body['items']]
    wanted = [cases[key] for key in expected]
    if 'sort=' in query:
        assert received == wanted
    else:
        assert set(received) == set(wanted)
    assert body['total'] == (2 if 'offset=1' in query else len(expected))
    assert response.headers['cache-control'] == 'no-store'


def test_only_one_simultaneous_correction_can_replace_a_version(review):
    client, app, seed = review
    case = seed()
    login(client, 'analyst')
    before = client.get(f'/review/{case}').json()
    def correct(description):
        with TestClient(app) as contender:
            csrf = login(contender, 'analyst')
            draft = deepcopy(before['extraction'])
            draft['lines'][0]['description'] = description
            response = contender.patch(f'/review/{case}', headers=headers(csrf, 0),
                                        json={'invoice': draft, 'comment': 'Correct description from original'})
            return description, response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, ['First correction', 'Second correction']))
    assert sorted(status for _, status in results) == [200, 409]
    current = client.get(f'/review/{case}').json()
    winner = next(description for description, status in results if status == 200)
    assert current['revision'] == 1 and current['extraction_version'] == 2
    assert current['extraction']['lines'][0]['description'] == winner
    assert current['original_extraction'] == before['extraction']
    events = client.get(f'/review/{case}/history').json()['events']
    versions = [event for event in events if event['kind'] == 'extraction']
    assert len(versions) == 2 and sum(event['current'] for event in versions) == 1


def test_rejection_is_audited_without_consuming_receipts(review):
    client, _, seed = review
    rejected, remaining = seed('REJECT'), seed('REMAINING', quantity='5', total='60')
    csrf = login(client, 'analyst')
    response = decision(client, csrf, rejected, 0, action='reject', comment='Original contains an incorrect charge')
    assert response.status_code == 200
    body = response.json()
    assert body['operational_state'] == 'REJECTED' and body['payment_blocked']
    assert body['evaluation']['result']['final_status'] == 'Held for Review'
    events = client.get(f'/review/{rejected}/history').json()['events']
    saved = [event for event in events if event['kind'] == 'decision']
    assert len(saved) == 1
    assert saved[0]['action'] == 'reject' and saved[0]['current']
    assert saved[0]['comment'] == 'Original contains an incorrect charge'
    assert saved[0]['actor_name'] == 'analyst' and saved[0]['actor_role'] == 'ap_analyst'
    assert decision(client, csrf, remaining, 0).status_code == 200


@pytest.mark.parametrize('variant,status', [('malformed', 422), ('oversize', 413)])
def test_invalid_or_oversize_json_is_private_and_leaves_case_unchanged(review, variant, status):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    before = client.get(f'/review/{case}/history').json()
    content = '{' if variant == 'malformed' else json.dumps({'invoice': {}, 'comment': 'x' * (300 * 1024)})
    response = client.patch(f'/review/{case}', content=content,
                            headers={**headers(csrf, 0), 'Content-Type': 'application/json'})
    assert response.status_code == status
    assert response.headers['cache-control'] == 'no-store'
    assert set(response.json()['error']) == {'code', 'message', 'retryable', 'correlation_id'}
    assert client.get(f'/review/{case}/history').json() == before
