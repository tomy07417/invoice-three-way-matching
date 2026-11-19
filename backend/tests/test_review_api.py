"""Public AP workflow against migrated, uniquely owned PostgreSQL databases."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app.application.dtos.auth import Role
from app.application.dtos.invoice_documents import ExtractionResult, InvoiceDraft, InvoiceLineDraft, TokenUsage
from app.application.dtos.reference_data import ReferenceData
from app.application.dtos.processing import ProcessingConfig
from app.application.use_cases.import_reference_data import import_reference_data
from app.application.use_cases.sessions import SessionService
from app.domain.entities import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, GoodsReceivedNoteLine
from app.infrastructure.extraction.document_validator import LocalDocumentValidator
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from app.infrastructure.processing.service import InvoiceService
from app.infrastructure.storage.local_documents import LocalDocumentStorage
from app.main import create_app
from test_invoice_list import login, upload, process


@pytest.fixture
def review(migrated_engine, request):
    now = [datetime(2027, 1, 1, tzinfo=timezone.utc)]
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    for username, role in [('owner', Role.INTAKE), ('analyst', Role.AP_ANALYST), ('manager', Role.AP_MANAGER)]:
        sessions.provision(username, 'long enough password', role)
    reference = ReferenceData(
        (Supplier('S1', 'Supplier', 'tax', 'USD', 'active', 'Address'),),
        (PurchaseOrder('PO-1', 'S1', date(2026, 10, 1), 'USD', 'open',
                       (PurchaseOrderLine(3, 'SKU', 'Item', Decimal('10'), Decimal('10')),)),),
        (GoodsReceivedNote('GRN-1', 'PO-1', date(2026, 10, 1), (GoodsReceivedNoteLine(3, Decimal('5')),)),))
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    config = getattr(request, 'param', ProcessingConfig())
    repo = SqlProcessingRepository(migrated_engine, config=config, clock=lambda: now[0])
    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=100)
        app = create_app(invoice_service=service)
        with TestClient(app) as client:
            csrf = login(client)
            def seed(number='AP-1', quantity='2', total='24', currency='USD', *, explicit=True, basis='net',
                     draft=None, document=None, extraction=None):
                # Price discrepancy produces a complete Held for Review without excess receipts.
                if draft is None:
                    draft = InvoiceDraft('Supplier', 'tax', number, date(2026, 10, 2), None, ('PO-1',), currency,
                                         Decimal(total), Decimal('0'), Decimal(total),
                                         (InvoiceLineDraft('Item', Decimal(quantity), Decimal('12'), Decimal(total),
                                                           '3' if explicit else None, 'PO-1' if explicit else None,
                                                           3 if explicit else None, basis),))
                if document is None:
                    admitted = upload(client, csrf, number)
                else:
                    response = client.post('/invoices', files={'file': document},
                                           headers={'X-CSRF-Token': csrf, 'Idempotency-Key': number})
                    assert response.status_code == 202
                    admitted = response.json()
                result = extraction if extraction is not None else ExtractionResult(True, draft, (), TokenUsage(1, 1))
                process(repo, storage, now, result)
                return admitted['invoice_id']
            yield client, app, seed


def headers(csrf, revision):
    return {'X-CSRF-Token': csrf, 'If-Match': f'"{revision}"'}


def decision(client, csrf, case, revision, action='approve', comment='Reviewed original and evidence'):
    return client.post(f'/review/{case}/decisions', headers=headers(csrf, revision),
                       json={'action': action, 'comment': comment})


def test_ap_queue_detail_and_role_boundary(review):
    client, _, seed = review
    case = seed()
    assert client.get('/review').status_code == 403  # Intake cannot gain AP access.
    csrf = login(client, 'analyst')
    result = client.get('/review')
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    assert result.json()['total'] == 1
    row, = result.json()['items']
    assert row['invoice_id'] == case and row['automatic_outcome'] == 'Held for Review'
    detail = client.get(f'/review/{case}')
    assert detail.status_code == 200 and detail.headers['etag'] == '"0"'
    body = detail.json()
    assert body['payment_blocked'] and body['revision'] == 0
    comparison, = body['comparison_rows']
    assert comparison['po_number'] == 'PO-1' and comparison['po_line_number'] == 3
    assert Decimal(comparison['quantity_available']) == Decimal('5')
    assert Decimal(comparison['quantity_invoiced']) == Decimal('2')
    assert body['eligibility']['allowed']
    assert client.get(body['document_url']).status_code == 200
    before = client.get(f'/review/{case}/history').json()
    denied = client.post(f'/review/{case}/decisions', headers={'If-Match': '"0"'},
                         json={'action': 'approve', 'comment': 'Attempt without CSRF'})
    assert denied.status_code == 403
    assert client.get(f'/review/{case}/history').json() == before
    assert decision(client, csrf, case, 0, comment='  ').status_code == 422
    assert client.get(f'/review/{case}/history').json() == before


def test_approved_correction_reverses_consumption_and_versions_history(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    original = client.get(f'/review/{case}').json()
    approved = decision(client, csrf, case, 0)
    assert approved.status_code == 200
    assert approved.json()['operational_state'] == 'APPROVED'
    assert not approved.json()['payment_blocked']
    draft = deepcopy(original['extraction'])
    draft['lines'][0]['quantity'] = '3'
    draft['lines'][0]['line_total'] = draft['subtotal'] = draft['total_amount'] = '36'
    corrected = client.patch(f'/review/{case}', headers=headers(csrf, 1),
                             json={'invoice': draft, 'comment': 'Quantity corrected against the original'})
    assert corrected.status_code == 200
    changed = corrected.json()
    assert changed['revision'] == 2 and changed['extraction_version'] == 2
    assert changed['requires_reevaluation'] and changed['payment_blocked']
    history_before = client.get(f'/review/{case}/history').json()
    assert decision(client, csrf, case, 2).status_code == 409
    assert client.get(f'/review/{case}/history').json() == history_before
    reevaluated = client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 2),
                              json={'comment': 'Evaluate corrected extraction'})
    assert reevaluated.status_code == 200
    body = reevaluated.json()
    assert body['revision'] == 3 and not body['requires_reevaluation']
    assert Decimal(body['comparison_rows'][0]['quantity_previously_approved']) == 0
    assert Decimal(body['comparison_rows'][0]['quantity_available']) == 5
    assert not any(f['code'] == 'DUPLICATE' and f['outcome'] == 'REJECT'
                   for f in body['evaluation']['result']['rules'])
    assert decision(client, csrf, case, 3).status_code == 200
    history = client.get(f'/review/{case}/history').json()['events']
    assert any(event['kind'] == 'approval_reversal' for event in history)
    decisions = [event for event in history if event['kind'] == 'decision']
    assert len(decisions) == 2 and sum(event['current'] for event in decisions) == 1
    original_evaluation = next(event for event in history if event['kind'] == 'evaluation')
    assert original_evaluation['evaluation_id'] == original['evaluation']['evaluation_id']
    assert client.get(f'/invoices/{case}').json()['evaluation']['evaluation_id'] == original['evaluation']['evaluation_id']


@pytest.mark.parametrize('amount,currency,allowed', [('5000', 'USD', True), ('5000.01', 'USD', False), ('1', 'EUR', False)])
def test_limit_is_enforced_through_api_and_manager_escalation(review, amount, currency, allowed):
    client, _, seed = review
    case = seed(total=amount, currency=currency)
    csrf = login(client, 'analyst')
    before = client.get(f'/review/{case}/history').json()
    result = decision(client, csrf, case, 0)
    assert result.status_code == (200 if allowed else 403)
    if not allowed:
        assert client.get(f'/review/{case}/history').json() == before
        escalated = client.post(f'/review/{case}/escalations', headers=headers(csrf, 0),
                                json={'comment': 'Approval requires Manager'})
        assert escalated.status_code == 200 and escalated.json()['operational_state'] == 'ESCALATED'
        csrf = login(client, 'manager')
        assert decision(client, csrf, case, escalated.json()['revision']).status_code == 200


def test_stale_duplicate_and_missing_version_have_no_side_effects(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    assert decision(client, csrf, case, 0).status_code == 200
    before = client.get(f'/review/{case}/history').json()
    assert decision(client, csrf, case, 0).status_code == 409
    assert decision(client, csrf, case, 1).status_code == 409
    assert client.post(f'/review/{case}/decisions', headers={'X-CSRF-Token': csrf},
                       json={'action': 'approve', 'comment': 'Missing version'}).status_code == 428
    assert client.get(f'/review/{case}/history').json() == before


def test_credit_note_is_a_persistent_payment_block_not_automatic_outcome(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    result = decision(client, csrf, case, 0, action='request_credit_note')
    assert result.status_code == 200
    body = result.json()
    assert body['operational_state'] == 'CREDIT_NOTE_REQUESTED'
    assert body['payment_blocked'] and body['credit_note_pending']
    assert body['evaluation']['result']['final_status'] == 'Held for Review'
    reevaluated = client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 1),
                              json={'comment': 'Refresh available evidence'})
    assert reevaluated.status_code == 200 and reevaluated.json()['credit_note_pending']
    before = client.get(f'/review/{case}/history').json()
    assert decision(client, csrf, case, 2).status_code == 409
    assert client.get(f'/review/{case}/history').json() == before


def test_competing_approvals_recheck_current_received_quantity(review):
    client, app, seed = review
    first = seed('FIRST', quantity='3', total='36')
    second = seed('SECOND', quantity='3', total='36')
    def approve(case):
        with TestClient(app) as contender:
            csrf = login(contender, 'analyst')
            return case, decision(contender, csrf, case, 0).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, [first, second]))
    assert sorted(status for _, status in results) == [200, 409]
    login(client, 'analyst')
    for case, status in results:
        detail = client.get(f'/review/{case}').json()
        assert detail['revision'] == (1 if status == 200 else 0)
        assert detail['payment_blocked'] == (status != 200)


def test_normalized_supplier_invoice_payment_dedup_is_transactional(review):
    client, _, seed = review
    first, second = seed('INV-123'), seed('inv123')
    csrf = login(client, 'manager')
    assert decision(client, csrf, first, 0).status_code == 200
    before = client.get(f'/review/{second}/history').json()
    result = decision(client, csrf, second, 0)
    assert result.status_code == 409
    assert client.get(f'/review/{second}/history').json() == before


def test_rejected_correction_and_ambiguous_approval_do_not_change_history(review):
    client, _, seed = review
    case = seed()
    csrf = login(client, 'analyst')
    detail = client.get(f'/review/{case}').json()
    before = client.get(f'/review/{case}/history').json()
    invalid = deepcopy(detail['extraction'])
    invalid['total_amount'] = 24.0
    assert client.patch(f'/review/{case}', headers=headers(csrf, 0),
                        json={'invoice': invalid, 'comment': 'Float input must be rejected'}).status_code == 422
    assert client.get(f'/review/{case}/history').json() == before
    ambiguous = deepcopy(detail['extraction'])
    ambiguous['lines'][0]['po_number'] = None
    assert client.patch(f'/review/{case}', headers=headers(csrf, 0),
                        json={'invoice': ambiguous, 'comment': 'Remove unverified association'}).status_code == 200
    assert client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 1),
                       json={'comment': 'Evaluate explicit missing association'}).status_code == 200
    before = client.get(f'/review/{case}/history').json()
    assert decision(client, csrf, case, 2).status_code == 409
    assert client.get(f'/review/{case}/history').json() == before
