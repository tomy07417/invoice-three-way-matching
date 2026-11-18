"""Public pure accounting inference; no PO prices or commercial tolerance inputs."""
from dataclasses import replace
from decimal import Decimal as D, localcontext
import pytest

from app.domain.matching.price_basis import (
    AccountingEvidence, Adjustment, PriceLine, resolve_price_basis,
)

LINES = (PriceLine(D('40'), D('24.50'), D('980.00'), 'unknown'),
         PriceLine(D('6'), D('89.00'), D('534.00'), 'unknown'))
EVIDENCE = AccountingEvidence(True, ())


def resolve(lines=LINES, subtotal=D('1514.00'), tax=D('317.94'), total=D('1831.94'), evidence=EVIDENCE, currency='USD'):
    return resolve_price_basis(lines, subtotal, tax, total, currency, evidence)


def test_inv01_infers_both_prices_without_mutating_facts():
    original = repr(LINES)
    result = resolve()
    assert [(line.basis, line.source) for line in result.lines] == [('net', 'arithmetic')] * 2
    assert result.reason == 'accounting_reconciled'
    assert result.expected_subtotal == D('1514.00') and result.expected_total == D('1831.94')
    assert repr(LINES) == original


def test_declared_net_or_gross_remains_declared_without_accounting_evidence():
    result = resolve(lines=tuple(replace(line, declared_basis='net') for line in LINES), evidence=None)
    assert [(line.basis, line.source) for line in result.lines] == [('net', 'declared')] * 2
    mixed = resolve(lines=(replace(LINES[0], declared_basis='gross'), LINES[1]))
    assert [(line.basis, line.source) for line in mixed.lines] == [('gross', 'declared'), ('unknown', 'undetermined')]


@pytest.mark.parametrize('kwargs', [
    {'subtotal': None}, {'tax': None}, {'total': None}, {'evidence': None},
    {'evidence': AccountingEvidence(None, ())}, {'evidence': AccountingEvidence(False, ())},
    {'evidence': AccountingEvidence(True, None)}, {'tax': D('0')},
    {'subtotal': D('1500')}, {'total': D('1832')}, {'currency': 'EUR'},
    {'lines': (replace(LINES[0], line_total=D('999')), LINES[1])},
    {'lines': (replace(LINES[0], quantity=None), LINES[1])},
    {'lines': (replace(LINES[0], unit_price=D('NaN')), LINES[1])},
    {'lines': (replace(LINES[0], quantity=D('-40')), LINES[1])},
    {'evidence': AccountingEvidence(True, (Adjustment('discount', 'unknown', D('1')),))},
    {'evidence': AccountingEvidence(True, (Adjustment('charge', 'before_subtotal', D('-1')),))},
    {'evidence': AccountingEvidence(True, (Adjustment('charge', 'before_subtotal', D('1'), 9),))},
])
def test_missing_conflicting_or_incomplete_evidence_stays_unknown(kwargs):
    assert all(line.basis == 'unknown' and line.source == 'undetermined' for line in resolve(**kwargs).lines)


@pytest.mark.parametrize('adjustment,subtotal,tax,total,line_total', [
    (Adjustment('discount', 'before_subtotal', D('10')), '90', '18.90', '108.90', '100'),
    (Adjustment('charge', 'before_subtotal', D('10')), '110', '23.10', '133.10', '100'),
    (Adjustment('discount', 'after_subtotal', D('10')), '100', '18.90', '108.90', '100'),
    (Adjustment('charge', 'after_subtotal', D('10')), '100', '23.10', '133.10', '100'),
    (Adjustment('discount', 'before_subtotal', D('-10'), 0), '90', '18.90', '108.90', '90'),
    (Adjustment('rounding', 'after_subtotal', D('-0.01')), '100', '21', '120.99', '100'),
])
def test_scoped_adjustments_are_applied_once_without_changing_amounts(adjustment, subtotal, tax, total, line_total):
    line = PriceLine(D('2'), D('50'), D(line_total), 'unknown')
    result = resolve((line,), D(subtotal), D(tax), D(total), AccountingEvidence(True, (adjustment,)))
    assert result.lines[0].basis == 'net' and result.reason == 'accounting_reconciled'
    assert line.unit_price == D('50') and adjustment.amount in (D('10'), D('-10'), D('-0.01'))


def test_rounding_is_half_up_per_line_and_independent_of_ambient_context():
    line = PriceLine(D('3'), D('0.335'), D('1.01'), 'unknown')
    with localcontext() as context:
        context.prec = 2
        result = resolve((line,), D('1.01'), D('0.21'), D('1.22'))
    assert result.lines[0].basis == 'net' and result.expected_subtotal == D('1.01')


@pytest.mark.parametrize('difference,expected', [('0.01', 'net'), ('0.0101', 'unknown'), ('0.02', 'unknown')])
def test_one_cent_boundary_is_absolute_not_two_percent(difference, expected):
    assert resolve(total=D('1831.94') + D(difference)).lines[0].basis == expected


def test_small_tax_cannot_disambiguate_net_from_gross_within_tolerance():
    result = resolve((PriceLine(D('1'), D('1.01'), D('1.01'), 'unknown'),), D('1'), D('0.01'), D('1.01'))
    assert result.lines[0].basis == 'unknown' and result.reason == 'ambiguous_tax_basis'


def test_reported_line_totals_must_also_reconcile_to_subtotal():
    lines = tuple(PriceLine(D('1'), D('1'), D('1.01'), 'unknown') for _ in range(3))
    assert resolve(lines, D('3'), D('0.63'), D('3.63')).lines[0].basis == 'unknown'
