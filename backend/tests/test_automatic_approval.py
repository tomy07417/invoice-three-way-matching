"""Automatic payment authorization through HTTP; never a real model/payment call."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.application.dtos.processing import ProcessingConfig
from app.domain.matching.price_basis import AccountingEvidence
from test_accounting_workflow import accounting_draft
from test_invoice_list import login
from test_manager_audit import audit, get_events
from test_review_api import review, headers, decision


def detail(client, case):
    response = client.get(f'/review/{case}')
    assert response.status_code == 200, response.text
    assert response.headers['etag'] == f'"{response.json()["revision"]}"'
    return response.json()


def reevaluate(client, csrf, case, revision):
    return client.post(f'/review/{case}/reevaluations', headers=headers(csrf, revision),
                       json={'comment': 'Recheck complete matching evidence'})


def correct_price(client, csrf, case, state):
    invoice = deepcopy(state['extraction'])
    invoice['lines'][0].update(unit_price='10', line_total='20')
    invoice.update(subtotal='20', total_amount='20')
    return client.patch(f'/review/{case}', headers=headers(csrf, state['revision']),
                        json={'invoice': invoice, 'comment': 'Correct the visible net unit price'})


def assert_automatic(state):
    assert state['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert state['operational_state'] == 'APPROVED'
    assert state['payment_blocked'] is False
    assert state['current_decision']['action'] == 'approve'
    assert state['current_decision']['actor_id'] == 'system:auto-pay'
    assert state['current_decision']['evaluation_id'] == state['evaluation']['evaluation_id']
    assert state['current_decision']['revision'] == state['revision']


def test_initial_auto_pay_is_authorized_once_and_attributed_to_system(audit):
    client, _, seed = audit
    draft = accounting_draft(AccountingEvidence(True, ()))
    case = seed(draft=draft)
    other = replace(draft, invoice_number='RECEIPT-PROBE', subtotal=Decimal('40'), tax_amount=Decimal('8.40'),
                    total_amount=Decimal('48.40'),
                    lines=(replace(draft.lines[0], quantity=Decimal('4'), line_total=Decimal('40')),))
    other_case = seed('RECEIPT-PROBE', draft=other)
    csrf = login(client, 'manager')
    state = detail(client, case)
    assert_automatic(state)
    assert state['revision'] == 1
    original_evaluation = deepcopy(state['evaluation'])
    page = get_events(client, case)
    approved, = [event for event in page['events'] if event['kind'] == 'decision']
    assert approved['actor'] == {'id': 'system:auto-pay', 'name': 'Automatic matching',
                                  'role': None, 'type': 'system'}
    assert approved['current'] is True and approved['revision'] == 1
    before = client.get(f'/review/{case}/history').json()
    approval, = [event for event in before['events'] if event['kind'] == 'decision']
    proof = approval['approval_time_quantities']
    assert len(proof) == 1
    assert Decimal(proof[0]['quantity']) == 2 and Decimal(proof[0]['previously_approved']) == 0
    evaluation, = [event for event in page['events'] if event['kind'] == 'evaluation']
    assert evaluation['actor']['type'] == 'recorded'  # Initiating owner is not the automatic approver.
    repeated = decision(client, csrf, case, state['revision'])
    assert repeated.status_code == 409
    assert repeated.json()['error']['code'] == 'decision_exists'
    assert detail(client, case) == state
    assert client.get(f'/review/{case}/history').json() == before
    assert get_events(client, case) == page
    for _ in range(2):
        response = reevaluate(client, csrf, case, state['revision'])
        assert response.status_code == 200, response.text
        state = response.json()
        assert_automatic(state)
        row, = state['comparison_rows']
        assert Decimal(row['quantity_previously_approved']) == 0  # Prior approval reversed first.
        assert Decimal(row['quantity_available']) == 5
    response = reevaluate(client, csrf, other_case, 0)
    assert response.status_code == 200, response.text
    held = response.json()
    assert held['evaluation']['result']['final_status'] == 'Held for Review'
    assert held['payment_blocked'] and held['current_decision'] is None
    assert Decimal(held['comparison_rows'][0]['quantity_previously_approved']) == 2
    assert Decimal(held['comparison_rows'][0]['quantity_available']) == 3
    historical = get_events(client, case, limit=100)
    first = next(event for event in historical['events'] if event['kind'] == 'evaluation'
                 and event['evaluation_id'] == original_evaluation['evaluation_id'])
    assert first['evidence']['evaluation'] == original_evaluation


def test_correction_then_auto_pay_reevaluation_approves_without_human_decision(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    initial = detail(client, case)
    assert initial['evaluation']['result']['final_status'] == 'Held for Review'
    changed = correct_price(client, csrf, case, initial)
    assert changed.status_code == 200, changed.text
    assert changed.json()['operational_state'] == 'REEVALUATION_REQUIRED'
    response = reevaluate(client, csrf, case, changed.json()['revision'])
    assert response.status_code == 200, response.text
    state = response.json()
    assert_automatic(state)
    assert state['revision'] == 2 and state['extraction_version'] == 2
    assert state['original_extraction'] == initial['original_extraction']
    history = client.get(f'/review/{case}/history').json()['events']
    approval, = [event for event in history if event['kind'] == 'decision']
    assert approval['actor_id'] == 'system:auto-pay' and approval['current'] is True
    assert approval['approval_time_quantities'][0]['quantity'] == '2'


def test_human_rejection_survives_unchanged_reevaluation_until_correction(review, migrated_engine):
    client, app, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    rejected = decision(client, csrf, case, 0, 'reject', 'Supplier invoice rejected by analyst')
    assert rejected.status_code == 200
    rejection = rejected.json()['current_decision']
    response = reevaluate(client, csrf, case, 1)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['operational_state'] == 'REJECTED' and state['payment_blocked'] is True
    assert state['current_decision'] == rejection
    # A future-only rule update can make unchanged facts Auto-Pay; rejection still wins.
    from app.application.use_cases.parameters import ParameterService
    from app.application.use_cases.sessions import SessionService
    from app.infrastructure.persistence.auth_repository import SqlAuthRepository
    from app.infrastructure.persistence.parameter_repository import SqlParameterRepository
    from app.interfaces.api.parameters import build_parameters_router
    app.include_router(build_parameters_router(ParameterService(SessionService(SqlAuthRepository(migrated_engine)),
                                                               SqlParameterRepository(migrated_engine))))
    csrf = login(client, 'manager')
    configuration = client.get('/manager/parameters').json()
    changed_rule = client.patch('/manager/parameters', headers=headers(csrf, configuration['version_id']),
        json={'price_tolerance': '.25', 'comment': 'Future evaluations accept this commercial discrepancy'})
    assert changed_rule.status_code == 200, changed_rule.text
    response = reevaluate(client, csrf, case, state['revision'])
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert state['operational_state'] == 'REJECTED' and state['payment_blocked']
    assert state['current_decision'] == rejection
    changed = correct_price(client, csrf, case, state)
    assert changed.status_code == 200, changed.text
    assert changed.json()['current_decision'] is None
    response = reevaluate(client, csrf, case, changed.json()['revision'])
    assert response.status_code == 200, response.text
    assert_automatic(response.json())
    decisions = [event for event in client.get(f'/review/{case}/history').json()['events']
                 if event['kind'] == 'decision']
    assert [event['current'] for event in decisions] == [False, True]
    assert decisions[0]['decision_id'] == rejection['id']


def test_pending_credit_note_blocks_automatic_approval_after_correction(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    requested = decision(client, csrf, case, 0, 'request_credit_note', 'Awaiting supplier credit note')
    assert requested.status_code == 200
    changed = correct_price(client, csrf, case, requested.json())
    assert changed.status_code == 200
    response = reevaluate(client, csrf, case, changed.json()['revision'])
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert state['operational_state'] == 'CREDIT_NOTE_REQUESTED'
    assert state['payment_blocked'] and state['credit_note_pending']
    assert state['current_decision'] is None


@pytest.mark.parametrize('review', [ProcessingConfig(max_active=2)], indirect=True)
def test_concurrent_initial_auto_pay_cannot_reserve_the_same_receipts(review, monkeypatch):
    from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
    client, _, seed = review
    ready = Barrier(2)
    complete = SqlProcessingRepository.complete

    def competing_completion(repository, claim, callback):
        ready.wait(timeout=10)
        return complete(repository, claim, callback)

    monkeypatch.setattr(SqlProcessingRepository, 'complete', competing_completion)
    draft = accounting_draft(AccountingEvidence(True, ()))
    other = replace(draft, invoice_number='RACE-4', subtotal=Decimal('40'), tax_amount=Decimal('8.40'),
                    total_amount=Decimal('48.40'),
                    lines=(replace(draft.lines[0], quantity=Decimal('4'), line_total=Decimal('40')),))
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = [workers.submit(seed, 'RACE-2', draft=replace(draft, invoice_number='RACE-2')),
                   workers.submit(seed, 'RACE-4', draft=other)]
        cases = [future.result(timeout=20) for future in pending]
    login(client, 'analyst')
    states = [detail(client, case) for case in cases]
    authorized, = [state for state in states if state['operational_state'] == 'APPROVED']
    held, = [state for state in states if state['evaluation']['result']['final_status'] == 'Held for Review']
    assert_automatic(authorized)
    assert held['payment_blocked'] and held['current_decision'] is None
    quantity = Decimal(authorized['extraction']['lines'][0]['quantity'])
    assert Decimal(held['comparison_rows'][0]['quantity_previously_approved']) == quantity
    assert Decimal(held['comparison_rows'][0]['quantity_available']) == 5 - quantity
