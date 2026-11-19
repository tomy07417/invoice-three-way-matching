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
    unchanged = client.patch(f'/review/{case}', headers=headers(csrf, initial['revision']),
        json={'invoice': initial['extraction'], 'comment': 'No change to accounting facts'})
    assert unchanged.status_code == 422
    assert unchanged.json()['error']['code'] == 'unchanged_correction'
    assert client.get(f'/review/{case}/history').json() == history
    assert client.get(f'/review/{case}').json() == initial
    corrected = deepcopy(initial['extraction']); corrected['due_date'] = '2026-12-01'
    response = client.patch(f'/review/{case}', headers=headers(csrf, initial['revision']),
        json={'invoice': corrected, 'comment': 'Add the visible due date only'})
    assert response.status_code == 200
    after = client.get(f'/review/{case}').json()
    assert after['extraction'] == corrected and after['revision'] == initial['revision'] + 1
    assert after['original_extraction'] == initial['original_extraction']
    assert after['extraction'].get('accounting_evidence') == initial['extraction'].get('accounting_evidence')


def test_arithmetic_basis_is_shared_by_reevaluation_and_fresh_approval(review):
    from test_review_api import decision
    client, _, seed = review
    draft = accounting_draft(AccountingEvidence(True, ()))
    case = seed(draft=draft)
    second = replace(draft, invoice_number='ACCOUNTING-2', subtotal=Decimal('40.00'),
        tax_amount=Decimal('8.40'), total_amount=Decimal('48.40'),
        lines=(replace(draft.lines[0], quantity=Decimal('4'), line_total=Decimal('40.00')),))
    second_case = seed('ACCOUNTING-2', draft=second)
    csrf = login(client, 'analyst')
    initial = client.get(f'/review/{case}').json()
    assert initial['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert initial['extraction'] == initial['original_extraction'] == review_value(draft)
    assert initial['comparison_rows'][0]['unit_price_basis_source'] == 'arithmetic'
    snapshot = deepcopy(initial['evaluation']['input_snapshot'])
    reevaluated = client.post(f'/review/{case}/reevaluations', headers=headers(csrf, initial['revision']),
        json={'comment': 'Recheck the same visible accounting facts'})
    assert reevaluated.status_code == 200
    after = reevaluated.json()
    assert after['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert after['evaluation']['input_snapshot'] == snapshot
    assert after['operational_state'] == 'APPROVED' and not after['payment_blocked']
    assert after['current_decision']['actor_id'] == 'system:auto-pay'
    assert after['extraction'] == initial['extraction']
    detail = client.get(f'/review/{second_case}').json()
    # Initial automatic approval already consumes receipts; manual overrides still recheck them.
    assert detail['evaluation']['result']['final_status'] == 'Held for Review'
    assert detail['comparison_rows'][0]['quantity_previously_approved'] == '2'
    history = client.get(f'/review/{second_case}/history').json()
    denied = decision(client, csrf, second_case, 0)
    assert denied.status_code == 409
    assert denied.json()['error']['code'] == 'insufficient_available_quantity'
    assert client.get(f'/review/{second_case}/history').json() == history
    assert client.get(f'/review/{second_case}').json() == detail
    refreshed = client.post(f'/review/{second_case}/reevaluations', headers=headers(csrf, 0),
        json={'comment': 'Observe the freshly consumed receipt quantities'})
    assert refreshed.status_code == 200
    assert refreshed.json()['evaluation']['result']['final_status'] == 'Held for Review'
    assert refreshed.json()['comparison_rows'][0]['quantity_previously_approved'] == '2'
    assert refreshed.json()['comparison_rows'][0]['quantity_available'] == '3'
