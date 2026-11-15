"""Deterministic evaluation of supplied facts; no I/O or payment authorization."""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Mapping

from ..entities import GoodsReceivedNote, Invoice, PurchaseOrder, Supplier
from . import rules
from .duplicates import check_duplicate_history
from .types import (
    BusinessStatus, EvaluationResult, InvoiceHistoryEntry, LineAssociation,
    MatchingConfig, RuleResult,
)


def _combined_quantity(invoice: Invoice, indices: list[int]) -> Decimal:
    values = tuple(invoice.lines[index].quantity for index in indices)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        return Decimal("NaN")
    with localcontext() as context:
        context.prec = rules._exact_precision(values)
        return sum(values, Decimal("0"))


def evaluate(
    invoice: Invoice,
    purchase_orders: tuple[PurchaseOrder, ...],
    grns: tuple[GoodsReceivedNote, ...],
    *,
    supplier: Supplier | None = None,
    associations: Mapping[int, LineAssociation | None] | None = None,
    approved_quantities: Mapping[LineAssociation, Decimal] | None = None,
    invoice_history: tuple[InvoiceHistoryEntry, ...] | None = None,
    net_unit_prices: Mapping[int, Decimal] | None = None,
    config: MatchingConfig = MatchingConfig(),
) -> EvaluationResult:
    """Evaluate available rules and retain explicit missing mandatory evidence.

    Application supplies actually received GRNs, approved-only PO-local totals,
    other-case invoice history and verified net unit prices. None means missing
    evidence; empty approved/history snapshots explicitly establish no records.
    An absent/invalid target cannot reliably associate a line and therefore holds
    the invoice. This evaluation never records approval or executes a payment.
    """
    orders: dict[str, PurchaseOrder] = {}
    for po in purchase_orders:
        if po.po_number in orders:
            raise ValueError("Duplicate PO number in evaluation context")
        if len({line.line_number for line in po.lines}) != len(po.lines):
            raise ValueError("Duplicate PO-local line number in evaluation context")
        orders[po.po_number] = po
    if len({note.grn_number for note in grns}) != len(grns):
        raise ValueError("Duplicate GRN number in evaluation context")
    links = dict(associations) if associations is not None else {}
    prices = dict(net_unit_prices) if net_unit_prices is not None else {}
    approved = dict(approved_quantities) if approved_quantities is not None else None
    for index in (*links, *prices):
        if type(index) is not int or not 0 <= index < len(invoice.lines):
            raise ValueError("Invoice line index is outside evaluation context")
    for link in links.values():
        if link is not None and not isinstance(link, LineAssociation):
            raise ValueError("Association must be a LineAssociation or None")
    if approved is not None and any(not isinstance(link, LineAssociation) for link in approved):
        raise ValueError("Approved quantities require PO-local LineAssociation keys")

    supplier_id = supplier.supplier_id if supplier is not None else None
    findings = [rules.check_po_references(invoice), rules.check_supplier_activity(supplier),
                *check_duplicate_history(invoice, supplier_id, invoice_history)]
    for reference in invoice.po_references:
        po = orders.get(reference)
        findings.append(rules.check_po_exists(reference, po))
        if po is not None:
            findings.extend((rules.check_supplier(supplier_id, po),
                             rules.check_invoice_date(invoice, po), rules.check_po_status(po),
                             rules.check_po_currency(invoice, po)))

    groups: dict[LineAssociation, list[int]] = defaultdict(list)
    for index, link in links.items():
        if link is not None:
            groups[link].append(index)
    quantities = {link: _combined_quantity(invoice, indices) for link, indices in groups.items()}
    for index, line in enumerate(invoice.lines):
        link = links.get(index)
        po = orders.get(link.po_number) if link is not None else None
        po_line = next((item for item in po.lines if item.line_number == link.po_line_number), None) if po else None
        if link is None or link.po_number not in invoice.po_references or po_line is None:
            findings.append(RuleResult("LINE_ASSOCIATION", "HOLD",
                                       "Invoice line has no unique reliable referenced PO-line association.",
                                       link.po_number if link else None, index,
                                       link.po_line_number if link else None))
            continue
        findings.append(RuleResult("LINE_ASSOCIATION", "PASS", "Caller supplied a reliable PO-line association.",
                                   link.po_number, index, link.po_line_number))
        if approved is None:
            findings.append(RuleResult("QUANTITY", "PENDING",
                                       "Approved-quantity snapshot has not been supplied.",
                                       link.po_number, index, link.po_line_number))
        else:
            grouped_line = replace(line, quantity=quantities[link])
            findings.append(rules.check_quantity(grouped_line, po, po_line, grns, index,
                                                 approved_quantity=approved.get(link, Decimal("0"))))
        if invoice.currency != po.currency:
            findings.append(RuleResult("UNIT_PRICE", "HOLD",
                                       "Invoice and PO currencies differ; no conversion is performed.",
                                       link.po_number, index, link.po_line_number))
        elif index not in prices:
            findings.append(RuleResult("UNIT_PRICE", "PENDING",
                                       "Net-of-tax unit price has not been supplied.",
                                       link.po_number, index, link.po_line_number))
        elif not isinstance(prices[index], Decimal) or not prices[index].is_finite():
            findings.append(RuleResult("UNIT_PRICE", "PENDING",
                                       "Net-of-tax unit price requires a finite Decimal.",
                                       link.po_number, index, link.po_line_number))
        else:
            findings.append(rules.check_price(replace(line, unit_price=prices[index]), po, po_line, config, index))

    # Corrections, decision validity, credit-note blocks and audit are separate
    # application workflows. A pure result never mutates a historical decision.
    provisional: BusinessStatus = "Auto-Pay"
    if any(result.outcome == "REJECT" for result in findings):
        provisional = "Rejected"
    elif any(result.outcome == "HOLD" for result in findings):
        provisional = "Held for Review"
    complete = not any(result.outcome == "PENDING" for result in findings)
    return EvaluationResult(provisional, provisional if complete else None, tuple(findings), config)
