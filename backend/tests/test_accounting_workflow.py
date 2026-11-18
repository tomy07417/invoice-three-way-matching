"""Public persistence/AP compatibility on disposable, owned PostgreSQL."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import pytest
from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft
from app.application.dtos.review import review_value
from app.domain.matching.price_basis import AccountingEvidence, Adjustment
from test_invoice_list import login
from test_review_api import review, headers


def accounting_draft(evidence):
    return InvoiceDraft('Supplier', 'tax', 'ACCOUNTING-1', date(2026, 10, 2), None,
        ('PO-1',), 'USD', Decimal('20.00'), Decimal('4.20'), Decimal('24.20'),
        (InvoiceLineDraft('Item', Decimal('2'), Decimal('10.00'), Decimal('20.00'),
                          '3', 'PO-1', 3, 'unknown'),), evidence)


@pytest.mark.parametrize('evidence', [None, AccountingEvidence(True, ()),
    AccountingEvidence(None, None), AccountingEvidence(True, (
        Adjustment('discount', 'after_subtotal', Decimal('-1.00')),))])
def test_persisted_accounting_facts_survive_get_corrections_and_noop_rejection(review, evidence):
    client, _, seed = review
    draft = accounting_draft(evidence)
    case = seed(draft=draft)
    csrf = login(client, 'analyst')
    response = client.get(f'/review/{case}')
    assert response.status_code == 200
    initial = response.json()
    assert initial['extraction'] == initial['original_extraction'] == review_value(draft)
    intake = client.get(f'/invoices/{case}')
    assert intake.status_code == 200
    # Public intake may expose optional null; AP canonicalizes it for no-op checks.
    extracted = intake.json()['extraction']['invoice']
    assert extracted.get('accounting_evidence') == initial['extraction'].get('accounting_evidence')
    history = client.get(f'/review/{case}/history').json()
    unchanged = client.patch(f'/review/{case}', headers=headers(csrf, 0),
        json={'invoice': initial['extraction'], 'comment': 'No change to accounting facts'})
    assert unchanged.status_code == 422
    assert unchanged.json()['error']['code'] == 'unchanged_correction'
    assert client.get(f'/review/{case}/history').json() == history
    assert client.get(f'/review/{case}').json() == initial
    corrected = deepcopy(initial['extraction']); corrected['due_date'] = '2026-12-01'
    response = client.patch(f'/review/{case}', headers=headers(csrf, 0),
        json={'invoice': corrected, 'comment': 'Add the visible due date only'})
    assert response.status_code == 200
    after = client.get(f'/review/{case}').json()
    assert after['extraction'] == corrected and after['revision'] == 1
    assert after['original_extraction'] == initial['original_extraction']
    assert after['extraction'].get('accounting_evidence') == initial['extraction'].get('accounting_evidence')
