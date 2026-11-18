"""Application evaluation uses effective prices, never rewrites extraction."""
from dataclasses import asdict, replace
from decimal import Decimal as D
import pytest
from app.application.dtos.review_comparison import comparison_rows
from app.domain.matching.price_basis import AccountingEvidence, Adjustment
from test_line_association_evaluation import sample, run
from test_evaluate_invoice_use_case import FakeRepository


@pytest.mark.parametrize('case,source,expected', [
    ('arithmetic', 'arithmetic', 'Auto-Pay'), ('declared', 'declared', 'Auto-Pay'),
    ('gross', 'declared', None), ('missing', 'undetermined', None),
    ('unknown_adjustments', 'undetermined', None), ('total_mismatch', 'undetermined', None),
    ('discount', 'arithmetic', 'Auto-Pay'), ('charge', 'arithmetic', 'Auto-Pay'),
    ('unscoped', 'undetermined', None), ('price_mismatch', 'arithmetic', 'Held for Review'),
])
def test_effective_price_basis_controls_existing_checks_and_audited_provenance(case, source, expected):
    draft = sample()
    draft = replace(draft, tax_amount=D('4.20'), total_amount=D('24.20'),
        accounting_evidence=AccountingEvidence(True, ()),
        lines=(replace(draft.lines[0], unit_price_basis='unknown'),))
    if case in ('declared', 'gross'):
        draft = replace(draft, lines=(replace(draft.lines[0], unit_price_basis='net' if case == 'declared' else 'gross'),))
    elif case == 'missing':draft = replace(draft, accounting_evidence=None)
    elif case == 'unknown_adjustments':draft = replace(draft, accounting_evidence=AccountingEvidence(True, None))
    elif case == 'total_mismatch':draft = replace(draft, total_amount=D('24.22'))
    elif case in ('discount', 'charge', 'unscoped'):
        kind = 'charge' if case == 'charge' else 'discount'
        stage = 'unknown' if case == 'unscoped' else 'after_subtotal'
        draft = replace(draft, total_amount=D('25.20') if kind == 'charge' else D('23.20'),
            accounting_evidence=AccountingEvidence(True, (Adjustment(kind, stage, D('1.00')),)))
    elif case == 'price_mismatch':
        draft = replace(draft, subtotal=D('22.00'), total_amount=D('26.20'),
            lines=(replace(draft.lines[0], unit_price=D('11.00'), line_total=D('22.00')),))
    before = asdict(draft)
    identifiers, record = run(FakeRepository(), draft)
    assert identifiers == (1, 1, expected is not None)
    assert record.result.final_status == expected
    assert asdict(draft) == before
    evidence = record.input_snapshot['price_basis']
    assert evidence['declared_bases'] == [draft.lines[0].unit_price_basis]
    assert evidence['resolution']['lines'][0]['source'] == source
    assert evidence['resolution']['tolerance'] == '0.01'
    assert evidence['resolution']['rounding'] == 'ROUND_HALF_UP'
    row, = comparison_rows(record.input_snapshot, record.context_snapshot, record.result.rules)
    assert row['unit_price_basis_source'] == source
    assert row['declared_unit_price_basis'] == draft.lines[0].unit_price_basis
    assert row['effective_unit_price_basis'] == ('net' if expected else 'gross' if case == 'gross' else 'unknown')
    assert record.input_snapshot['invoice']['total_amount'] == str(draft.total_amount)
    assert record.result.config.price_tolerance == D('0.02')
    assert any(rule.code == 'UNIT_PRICE' and rule.outcome == (
        'PENDING' if expected is None else 'HOLD' if case == 'price_mismatch' else 'PASS') for rule in record.result.rules)
