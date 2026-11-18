"""Public-interface regressions promoted from independent AP verification."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
import subprocess
import sys

from test_invoice_list import login
from test_review_api import review, decision, headers


def fingerprint(client, case):
    # Public persisted state/history, not private-table business assertions.
    state = [client.get(f'/review/{case}').json(), client.get(f'/review/{case}/history').json()]
    return sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def test_shared_target_release_is_visible_to_other_cases(review):
    # Two rows cannot each spend the same available five units.
    client, _, seed = review
    case = seed('GROUPED')
    other = seed('OTHER', quantity='5', total='60')
    csrf = login(client, 'analyst')
    draft = client.get(f'/review/{case}').json()['extraction']
    draft['lines'] = [deepcopy(draft['lines'][0]) for _ in range(2)]
    for line in draft['lines']:
        line.update(quantity='3', line_total='36')
    draft.update(subtotal='72', total_amount='72')
    assert client.patch(f'/review/{case}', headers=headers(csrf, 0), json={
        'invoice': draft, 'comment': 'Split original into two shared-target rows',
    }).status_code == 200
    assert client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 1), json={
        'comment': 'Evaluate grouped quantities',
    }).status_code == 200
    before = fingerprint(client, case)
    denied = decision(client, csrf, case, 2)
    assert denied.status_code == 409
    assert denied.json()['error']['code'] == 'insufficient_available_quantity'
    assert fingerprint(client, case) == before
    draft['lines'][1].update(quantity='2', line_total='24')
    draft.update(subtotal='60', total_amount='60')
    assert client.patch(f'/review/{case}', headers=headers(csrf, 2), json={
        'invoice': draft, 'comment': 'Correct aggregate to exactly five',
    }).status_code == 200
    assert client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 3), json={
        'comment': 'Evaluate exact inclusive availability',
    }).status_code == 200
    assert decision(client, csrf, case, 4).status_code == 200
    before = fingerprint(client, other)
    assert decision(client, csrf, other, 0).status_code == 409
    assert fingerprint(client, other) == before
    draft['lines'][0]['description'] = 'Corrected description'
    assert client.patch(f'/review/{case}', headers=headers(csrf, 5), json={
        'invoice': draft, 'comment': 'Invalidate and release old authorization',
    }).status_code == 200
    assert client.get(f'/review/{case}').json()['payment_blocked']
    assert decision(client, csrf, other, 0).status_code == 200
    events = client.get(f'/review/{case}/history').json()['events']
    assert len([event for event in events if event['kind'] == 'approval_reversal']) == 1
    assert not any(event['current'] for event in events if event['kind'] == 'decision')


def test_later_receipt_cli_does_not_rewrite_evaluation(review, migrated_engine, tmp_path):
    # Later receipts count at authorization; the comparison snapshot remains immutable.
    client, _, seed = review
    case = seed('LATER', quantity='6', total='72')
    csrf = login(client, 'analyst')
    original = client.get(f'/review/{case}').json()
    before = fingerprint(client, case)
    assert decision(client, csrf, case, 0).status_code == 409
    assert fingerprint(client, case) == before
    for name in ('suppliers', 'purchase_orders'):
        (tmp_path / f'{name}.json').write_text('[]')
    (tmp_path / 'goods_received_notes.json').write_text(json.dumps([{
        'grn_number': 'LATER-GRN', 'po_number': 'PO-1', 'received_date': '2027-01-01',
        'lines': [{'po_line_number': 3, 'quantity_received': 1}],
    }]))
    env = {**os.environ, 'DATABASE_URL': migrated_engine.url.render_as_string(hide_password=False)}
    result = subprocess.run([
        sys.executable, '-m', 'app.interfaces.cli.import_reference_data', '--directory', str(tmp_path),
    ], env=env, text=True, capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        '{"suppliers":{"inserted":0,"unchanged":0},"purchase_orders":{"inserted":0,"unchanged":0},'
        '"goods_received_notes":{"inserted":1,"unchanged":0}}\n',
        '',
    )
    assert client.get(f'/review/{case}').json() == original
    approved = decision(client, csrf, case, 0)
    assert approved.status_code == 200 and not approved.json()['payment_blocked']
    assert approved.json()['comparison_rows'] == original['comparison_rows']
    event = next(event for event in client.get(f'/review/{case}/history').json()['events']
                 if event['kind'] == 'decision')
    assert Decimal(event['approval_time_quantities'][0]['received']) == 6
