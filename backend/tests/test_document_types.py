"""Document disposition through public HTTP, controlled extraction, no Azure calls."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.dtos.invoice_documents import ExtractionResult, InvoiceDraft, InvoiceLineDraft, TokenUsage
from app.application.use_cases.evaluate_invoice import EvaluateInvoiceUseCase
from test_invoice_list import login
from test_review_api import review, headers
from app.application.dtos.review import review_value

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('kind,expected,rule,matched', [
    ('quotation', 'Rejected', 'INVALID_DOCUMENT_TYPE', False),
    ('unknown', 'Held for Review', 'DOCUMENT_TYPE_UNCERTAIN', False),
    ('invoice', 'Auto-Pay', None, True),
    ('incomplete_invoice', None, 'missing_evidence', False),
    ('uncertain_invoice', 'Held for Review', 'DOCUMENT_TYPE_UNCERTAIN', False),
    (None, None, 'missing_evidence', False),
])
def test_document_type_disposition_survives_persistence_and_skips_matching(
        review, monkeypatch, kind, expected, rule, matched):
    client, _, seed = review
    calls = []
    original = EvaluateInvoiceUseCase.execute

    def tracked(self, command):
        calls.append(command)
        return original(self, command)

    monkeypatch.setattr(EvaluateInvoiceUseCase, 'execute', tracked)
    draft = None
    if kind in ('invoice', 'incomplete_invoice', 'uncertain_invoice'):
        draft = InvoiceDraft('Supplier', 'tax', 'VALID-1', date(2026, 10, 2), None, ('PO-1',), 'USD',
                             Decimal('20'), Decimal('0'), Decimal('20'),
                             (InvoiceLineDraft('Item', Decimal('2'), Decimal('10'), Decimal('20'),
                                               None, 'PO-1', 3, 'net'),))
    if kind == 'incomplete_invoice':
        draft = replace(draft, supplier_name=None)
    document_type = 'invoice' if kind == 'incomplete_invoice' else 'unknown' if kind == 'uncertain_invoice' else kind
    extraction = ExtractionResult(draft is not None, draft, (), TokenUsage(1, 1), document_type=document_type)
    path = ROOT / 'data/invoices/invoices/INV-10.pdf'
    case = seed(f'document-{kind}', extraction=extraction,
                document=(path.name, path.read_bytes(), 'application/pdf'))
    detail = client.get(f'/invoices/{case}')
    assert detail.status_code == 200
    value = detail.json()
    assert value['automatic_outcome'] == expected
    assert bool(calls) is matched
    assert len(calls) == int(matched)
    if draft is None:
        assert value['extraction']['invoice'] is None
    else:
        assert value['extraction']['invoice'] is not None
    if rule:
        assert any(item['code'] == rule for item in value['findings'])
        assert value['evaluation'] is None  # No invented invoice/evaluation/FK.
    if kind == 'quotation':
        assert value['processing_status'] == 'COMPLETED'
        assert value['findings'][0]['outcome'] == 'REJECT'
        assert 'quotation' in value['findings'][0]['reason'].lower()
    assert client.get('/invoices').json()['items'][0]['automatic_outcome'] == expected
    projected = expected if document_type in ('quotation', 'unknown') else None
    assert client.get(f'/invoices/{case}/processing').json()['automatic_outcome'] == projected
    login(client, 'analyst')
    ap = client.get(f'/review/{case}')
    assert ap.status_code == 200
    if kind in ('quotation', 'unknown', 'uncertain_invoice'):
        assert value['extraction']['document_type'] == document_type
        assert ap.json()['document_validation']['automatic_outcome'] == expected
        queue = client.get('/review', params={'outcome': expected})
        assert queue.status_code == 200
        assert [item['invoice_id'] for item in queue.json()['items']] == [case]
        assert queue.json()['items'][0]['reasons']
    if kind == 'quotation':
        assert ap.json()['document_validation']['automatic_outcome'] == expected
        assert ap.json()['operational_state'] == 'REJECTED'
        assert ap.json()['payment_blocked'] is True
        history = client.get(f'/review/{case}/history').json()['events']
        assert any(event['kind'] == 'document_validation' for event in history)
        assert not any(event['kind'] == 'evaluation' for event in history)
        login(client, 'manager')
        audit = client.get(f'/audit/{case}', params={'kind': 'document_validation'})
        assert audit.status_code == 200
        assert len(audit.json()['events']) == 1
        event = audit.json()['events'][0]
        assert event['actor']['type'] == 'system'
        assert event['actor']['id'] == 'system:document-validation'
        assert event['evidence']['document_validation']['automatic_outcome'] == 'Rejected'


@pytest.mark.parametrize('operation', ['correct', 'reevaluate', 'approve'])
def test_invoice_actions_cannot_silently_reclassify_a_quotation(review, operation):
    client, _, seed = review
    case = seed('quotation-guard', extraction=ExtractionResult(False, None, (), document_type='quotation'))
    csrf = login(client, 'analyst')
    before = client.get(f'/review/{case}').json()
    history = client.get(f'/review/{case}/history').json()
    request_headers = headers(csrf, before['revision'])
    if operation == 'correct':
        draft = InvoiceDraft('Supplier', 'tax', 'AP-CORRECTED', date(2026, 10, 2), None, ('PO-1',), 'USD',
                             Decimal('20'), Decimal('0'), Decimal('20'),
                             (InvoiceLineDraft('Item', Decimal('2'), Decimal('10'), Decimal('20'),
                                               None, 'PO-1', 3, 'net'),))
        response = client.patch(f'/review/{case}', headers=request_headers,
            json={'invoice': review_value(draft), 'comment': 'Attempt to replace quotation with invoice fields'})
    elif operation == 'reevaluate':
        response = client.post(f'/review/{case}/reevaluations', headers=request_headers,
                               json={'comment': 'Retry must retain original document type'})
    else:
        response = client.post(f'/review/{case}/decisions', headers=request_headers,
                               json={'action': 'approve', 'comment': 'Must never approve a quotation'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'document_type_blocked'
    assert client.get(f'/review/{case}').json() == before
    assert client.get(f'/review/{case}/history').json() == history
