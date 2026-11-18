"""Exact optional accounting evidence shared by extraction and AP correction."""
from decimal import Decimal
from app.domain.matching.price_basis import AccountingEvidence, Adjustment


def parse_accounting_evidence(value, decimal_parser=Decimal):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {'taxes_separately_stated', 'adjustments'}:
        raise ValueError('Invalid accounting evidence')
    separated, raw = value['taxes_separately_stated'], value['adjustments']
    if separated is not None and type(separated) is not bool:
        raise ValueError('Invalid tax separation evidence')
    if raw is None:
        return AccountingEvidence(separated, None)
    if type(raw) is not list or len(raw) > 500:
        raise ValueError('Invalid accounting adjustments')
    adjustments = []
    for item in raw:
        if type(item) is not dict or set(item) != {'kind', 'stage', 'amount', 'line_index'}:
            raise ValueError('Invalid accounting adjustment')
        kind, stage, amount, index = (item[key] for key in ('kind', 'stage', 'amount', 'line_index'))
        if kind not in ('discount', 'charge', 'rounding', 'unknown') or stage not in ('before_subtotal', 'after_subtotal', 'unknown'):
            raise ValueError('Invalid accounting adjustment type')
        if index is not None and (type(index) is not int or not 0 <= index < 500):
            raise ValueError('Invalid accounting line index')
        if amount is not None:
            if type(amount) is not str:
                raise ValueError('Accounting amounts require exact decimal strings')
            amount = decimal_parser(amount)
            if not isinstance(amount, Decimal) or not amount.is_finite():
                raise ValueError('Invalid accounting amount')
        adjustments.append(Adjustment(kind, stage, amount, index))
    return AccountingEvidence(separated, tuple(adjustments))
