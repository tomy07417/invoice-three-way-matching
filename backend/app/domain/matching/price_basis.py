"""Accounting evidence, separate from PO matching and commercial tolerance.

Amounts remain untouched. Only the effective classification is derived. Unknown
adjustment scope is not zero; explicit gross evidence is never overridden.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext, MAX_EMAX, MIN_EMIN
from typing import Literal

CENT = Decimal('0.01')


@dataclass(frozen=True)
class Adjustment:
    kind: Literal['discount', 'charge', 'rounding', 'unknown']
    stage: Literal['before_subtotal', 'after_subtotal', 'unknown']
    amount: Decimal | None
    line_index: int | None = None


@dataclass(frozen=True)
class AccountingEvidence:
    taxes_separately_stated: bool | None
    adjustments: tuple[Adjustment, ...] | None


@dataclass(frozen=True)
class PriceLine:
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    declared_basis: str


@dataclass(frozen=True)
class EffectivePriceBasis:
    basis: str
    source: str


@dataclass(frozen=True)
class PriceBasisResolution:
    lines: tuple[EffectivePriceBasis, ...]
    reason: str
    expected_subtotal: Decimal | None = None
    expected_total: Decimal | None = None
    tolerance: Decimal = CENT
    rounding: str = 'ROUND_HALF_UP'


def resolve_price_basis(lines: tuple[PriceLine, ...], subtotal, tax, total, currency,
                        evidence: AccountingEvidence | None) -> PriceBasisResolution:
    """Infer unknown bases only from complete, unambiguous USD reconciliation.

    Explicit bases keep their declared provenance, even when arithmetic cannot
    establish a new inference. The matching engine still owns all PO checks.
    """
    declared = tuple(EffectivePriceBasis(line.declared_basis, 'declared')
                     if line.declared_basis in ('net', 'gross')
                     else EffectivePriceBasis('unknown', 'undetermined') for line in lines)

    def unknown(reason, expected_subtotal=None, expected_total=None):
        return PriceBasisResolution(declared, reason, expected_subtotal, expected_total)

    if not lines or currency != 'USD':
        return unknown('unsupported_accounting_context')
    if any(line.declared_basis == 'gross' for line in lines):
        return unknown('explicit_gross_evidence')
    if evidence is None or evidence.taxes_separately_stated is not True or evidence.adjustments is None:
        return unknown('missing_accounting_evidence')
    values = [subtotal, tax, total]
    for line in lines:
        values.extend((line.quantity, line.unit_price, line.line_total))
    values.extend(item.amount for item in evidence.adjustments)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        return unknown('missing_or_nonfinite_amount')
    if subtotal <= 0 or tax <= 0 or total <= 0 or any(
            line.quantity <= 0 or line.unit_price <= 0 or line.line_total < 0 for line in lines):
        return unknown('ambiguous_or_nonpositive_amount')
    # Bound optional inference work, not accepted monetary values. Extreme
    # evidence remains unchanged/unknown rather than exhausting decimal memory.
    precision = 20 + sum(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in values)
    if precision > 4096:
        return unknown('accounting_precision_unavailable')
    with localcontext() as context:
        context.prec = max(28, precision)
        context.rounding, context.Emax, context.Emin = ROUND_HALF_UP, MAX_EMAX, MIN_EMIN
        before = after = Decimal(0)
        line_adjustments = [Decimal(0) for _ in lines]
        for item in evidence.adjustments:
            if (item.kind not in ('discount', 'charge', 'rounding')
                    or item.stage not in ('before_subtotal', 'after_subtotal')
                    or (item.kind == 'charge' and item.amount < 0)):
                return unknown('unresolved_adjustment')
            effect = item.amount.copy_abs().copy_negate() if item.kind == 'discount' else item.amount
            if item.line_index is not None:
                if (type(item.line_index) is not int or not 0 <= item.line_index < len(lines)
                        or item.stage != 'before_subtotal'):
                    return unknown('unresolved_adjustment')
                line_adjustments[item.line_index] += effect
            elif item.stage == 'before_subtotal':
                before += effect
            else:
                after += effect
        computed = tuple((line.quantity * line.unit_price + adjustment).quantize(CENT)
                         for line, adjustment in zip(lines, line_adjustments))
        expected_subtotal = sum(computed, before)
        reported_subtotal = sum((line.line_total for line in lines), before)
        expected_total = subtotal + tax + after
        if any(abs(value - line.line_total) > CENT for value, line in zip(computed, lines)):
            return unknown('line_amount_mismatch', expected_subtotal, expected_total)
        if abs(expected_subtotal - subtotal) > CENT or abs(reported_subtotal - subtotal) > CENT:
            return unknown('subtotal_mismatch', expected_subtotal, expected_total)
        if abs(expected_total - total) > CENT:
            return unknown('total_mismatch', expected_subtotal, expected_total)
        if abs(expected_subtotal - (subtotal + tax)) <= CENT:
            return unknown('ambiguous_tax_basis', expected_subtotal, expected_total)
        resolved = tuple(EffectivePriceBasis('net', 'arithmetic') if line.basis == 'unknown' else line
                         for line in declared)
        return PriceBasisResolution(resolved, 'accounting_reconciled', expected_subtotal, expected_total)
