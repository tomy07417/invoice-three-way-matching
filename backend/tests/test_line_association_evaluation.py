"""Application rules with controlled extraction evidence, never model claims."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.dtos.invoice_documents import ExtractionResult, InvoiceDraft, InvoiceLineDraft
from app.application.use_cases.process_invoice import evaluate_draft
from app.domain.matching.types import InvoiceHistoryEntry
from test_evaluate_invoice_use_case import FakeRepository, DAY, AT, LINK


def sample():
    return InvoiceDraft('Supplier', 'other-tax', 'ASSOC-1', DAY, None, ('PO-1',), 'USD',
                        Decimal('20.00'), Decimal('0'), Decimal('20.00'),
                        (InvoiceLineDraft('Item', Decimal('2'), Decimal('10'), Decimal('20'),
                                          None, None, None, 'net'),))


def run(repository, draft):
    with repository.transaction() as transaction:
        identifiers = evaluate_draft(transaction, SimpleNamespace(owner_id=1,
            extraction=ExtractionResult(True, draft, ())), lambda: AT)
    return identifiers, repository.records[-1] if repository.records else None


@pytest.mark.parametrize('case,expected,rule', [
    ('baseline', 'Auto-Pay', 'LINE_ASSOCIATION'),
    ('price', 'Held for Review', 'UNIT_PRICE'),
    ('quantity', 'Held for Review', 'QUANTITY'),
    ('prior_approved', 'Held for Review', 'QUANTITY'),
    ('multiple_grns', 'Auto-Pay', 'QUANTITY'),
    ('shared_target', 'Held for Review', 'QUANTITY'),
    ('missing_po_line', 'Held for Review', 'LINE_ASSOCIATION'),
    ('duplicate_rejection', 'Rejected', 'DUPLICATE'),
    ('currency', 'Held for Review', 'PO_CURRENCY'),
    ('unknown_basis', None, 'UNIT_PRICE'),
    ('historical_wrong_total', None, 'UNIT_PRICE'),
])
def test_resolved_identity_does_not_hide_independent_commercial_checks(case, expected, rule):
    repo, draft = FakeRepository(), sample()
    line = draft.lines[0]
    if case == 'price':
        draft = replace(draft, lines=(replace(line, unit_price=Decimal('11')),))
    elif case == 'quantity':
        draft = replace(draft, lines=(replace(line, quantity=Decimal('6')),))
    elif case == 'prior_approved':
        repo.approved = {LINK: Decimal('4')}
    elif case == 'multiple_grns':
        first = repo.reference.goods_received_notes[0]
        repo.reference = replace(repo.reference, goods_received_notes=(
            replace(first, lines=(replace(first.lines[0], quantity_received=Decimal('1')),)),
            replace(first, grn_number='GRN-2', lines=(replace(first.lines[0], quantity_received=Decimal('1')),))))
    elif case == 'shared_target':
        draft = replace(draft, lines=(replace(line, quantity=Decimal('3')), replace(line, quantity=Decimal('3'))))
    elif case == 'missing_po_line':
        repo.reference = replace(repo.reference, purchase_orders=(replace(repo.reference.purchase_orders[0], lines=()),))
    elif case == 'duplicate_rejection':
        repo.history = (InvoiceHistoryEntry('S1', 'ASSOC-1', DAY, 'USD', Decimal('999')),)
        draft = replace(draft, lines=(replace(line, unit_price=Decimal('99')),))
    elif case == 'currency':
        draft = replace(draft, currency='EUR')
    elif case in ('unknown_basis', 'historical_wrong_total'):
        draft = replace(draft, lines=(replace(line, unit_price_basis='unknown'),))
        if case == 'historical_wrong_total':
            draft = replace(draft, subtotal=Decimal('1.51400'), total_amount=Decimal('1.83194'))
    identifiers, record = run(repo, draft)
    assert identifiers == (1, 1, expected is not None)
    assert record.result.final_status == expected
    assert any(result.code == rule and result.outcome == (
        'PENDING' if expected is None else 'REJECT' if expected == 'Rejected'
        else 'HOLD' if expected == 'Held for Review' else 'PASS') for result in record.result.rules)
    if case != 'missing_po_line':
        assert all(result.outcome == 'PASS' for result in record.result.rules if result.code == 'LINE_ASSOCIATION')
    assert draft.lines[0].po_number is None
    assert record.input_snapshot['invoice']['total_amount'] == str(draft.total_amount)


def test_incomplete_extraction_does_not_create_an_evaluation_or_invent_missing_values():
    repo = FakeRepository()
    draft = sample()
    draft = replace(draft, lines=(replace(draft.lines[0], quantity=None),))
    identifiers, record = run(repo, draft)
    assert identifiers == (None, None, False) and record is None
    assert repo.invoices == {} and repo.records == []
