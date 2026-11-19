"""Independent public effects: automatic-approval S1/S2."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from threading import Barrier

from fastapi.testclient import TestClient
from app.domain.matching.price_basis import AccountingEvidence
from test_accounting_workflow import accounting_draft
from test_invoice_list import login
from test_review_api import review, headers, decision


def state(client, case):
    response = client.get(f'/review/{case}')
    assert response.status_code == 200, response.text
    return response.json()


def recheck(client, csrf, case, revision):
    return client.post(f'/review/{case}/reevaluations', headers=headers(csrf, revision),
                       json={'comment': 'Independent current-balance check'})


def correction(client, csrf, case, before, quantity):
    draft = deepcopy(before['extraction'])
    total = str(Decimal(quantity) * 10)
    draft['lines'][0].update(quantity=quantity, unit_price='10', line_total=total)
    tax = Decimal(total) * Decimal('.21') if draft.get('accounting_evidence') else Decimal(0)
    draft.update(subtotal=total, tax_amount=str(tax), total_amount=str(Decimal(total) + tax))
    return client.patch(f'/review/{case}', headers=headers(csrf, before['revision']),
                        json={'invoice': draft, 'comment': 'Correct quantity and net price'})


def test_exact_capacity_releases_after_correction_and_rejects_stale_write(review):
    # S1: exactly all receipts, then release/reconsume without duplicate ACTIVE quantities.
    client, _, seed = review
    draft = accounting_draft(AccountingEvidence(True, ()))
    full = replace(draft, subtotal=Decimal('50'), tax_amount=Decimal('10.50'),
                   total_amount=Decimal('60.50'),
                   lines=(replace(draft.lines[0], quantity=Decimal('5'), line_total=Decimal('50')),))
    case = seed(draft=full)
    other = replace(draft, invoice_number='EXACT-REST', subtotal=Decimal('30'),
                    tax_amount=Decimal('6.30'), total_amount=Decimal('36.30'),
                    lines=(replace(draft.lines[0], quantity=Decimal('3'), line_total=Decimal('30')),))
    second = seed('EXACT-REST', draft=other)
    csrf = login(client, 'analyst')
    before = state(client, case)
    assert before['operational_state'] == 'APPROVED' and not before['payment_blocked']
    assert before['current_decision']['actor_id'] == 'system:auto-pay'
    history = client.get(f'/review/{case}/history').content
    rejected = decision(client, csrf, case, 0)
    assert rejected.status_code == 409
    assert rejected.json()['error']['code'] == 'stale_revision'
    assert state(client, case) == before
    assert client.get(f'/review/{case}/history').content == history
    changed = correction(client, csrf, case, before, '2')
    assert changed.status_code == 200, changed.text
    approved = recheck(client, csrf, case, changed.json()['revision'])
    assert approved.status_code == 200, approved.text
    after = approved.json()
    assert after['operational_state'] == 'APPROVED' and not after['payment_blocked'], after['evaluation']['result']
    assert after['original_extraction'] == before['original_extraction']
    refreshed = recheck(client, csrf, second, 0)
    assert refreshed.status_code == 200, refreshed.text
    rest = state(client, second)
    assert rest['operational_state'] == 'APPROVED' and not rest['payment_blocked']
    assert Decimal(rest['comparison_rows'][0]['quantity_previously_approved']) == 2
    events = client.get(f'/review/{case}/history').json()['events']
    assert sum(e['kind'] == 'approval_reversal' for e in events) == 1
    assert sum(e['kind'] == 'decision' and e['current'] for e in events) == 1
    assert next(e for e in events if e['kind'] == 'evaluation')['evaluation'] == before['evaluation']


def test_competing_public_reevaluations_share_fresh_receipts(review):
    # S1: AP reevaluation must serialize before balances, not only initial processing.
    client, app, seed = review
    cases = [seed('AP-RACE-A', quantity='3', total='36'),
             seed('AP-RACE-B', quantity='3', total='36')]
    csrf = login(client, 'analyst')
    for case in cases:
        changed = correction(client, csrf, case, state(client, case), '3')
        assert changed.status_code == 200, changed.text
    ready = Barrier(2)
    def run(case):
        with TestClient(app) as contender:
            token = login(contender, 'analyst')
            ready.wait(timeout=10)
            response = recheck(contender, token, case, 1)
            assert response.status_code == 200, response.text
            return response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, cases))
    approved, = [s for s in results if s['operational_state'] == 'APPROVED']
    held, = [s for s in results if s['operational_state'] != 'APPROVED']
    assert approved['current_decision']['actor_id'] == 'system:auto-pay'
    assert not approved['payment_blocked']
    assert held['evaluation']['result']['final_status'] == 'Held for Review'
    assert held['payment_blocked'] and held['current_decision'] is None
    assert Decimal(held['comparison_rows'][0]['quantity_previously_approved']) == 3
    assert Decimal(held['comparison_rows'][0]['quantity_available']) == 2


def test_noop_is_not_a_correction_releasing_human_rejection(review):
    # S2: a rejected unchanged PATCH cannot invalidate the applicable human rejection.
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    rejected = decision(client, csrf, case, 0, 'reject', 'Supplier correction required')
    assert rejected.status_code == 200
    before = state(client, case)
    history = client.get(f'/review/{case}/history').content
    noop = client.patch(f'/review/{case}', headers=headers(csrf, before['revision']),
                        json={'invoice': before['extraction'], 'comment': 'No factual correction'})
    assert noop.status_code == 422
    assert noop.json()['error']['code'] == 'unchanged_correction'
    assert state(client, case) == before
    assert client.get(f'/review/{case}/history').content == history
    refreshed = recheck(client, csrf, case, before['revision'])
    assert refreshed.status_code == 200, refreshed.text
    after = refreshed.json()
    assert after['operational_state'] == 'REJECTED' and after['payment_blocked']
    assert after['current_decision'] == before['current_decision']
