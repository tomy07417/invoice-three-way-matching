"""Deterministic snapshot evaluation; no I/O, identity inference or persistence."""
from collections import Counter
from typing import Mapping

from ..entities import GoodsReceivedNote, Invoice, PurchaseOrder
from . import rules
from .types import (
    BusinessStatus, EvaluationResult, LineAssociation, MatchingConfig, PolicyFacts, RuleResult,
)
from .pending_policies import check_policy_facts


def evaluate(
    invoice: Invoice,
    purchase_orders: tuple[PurchaseOrder, ...],
    grns: tuple[GoodsReceivedNote, ...],
    *,
    resolved_supplier_id: str | None = None,
    associations: Mapping[int, LineAssociation | None] | None = None,
    policies: PolicyFacts = PolicyFacts(),
    config: MatchingConfig = MatchingConfig(),
) -> EvaluationResult:
    """Evaluate every available check, even when another rejects or is pending.

    Association keys are zero-based invoice line indices. Absent key = unresolved;
    None = confirmed no association; a fact = unequivocal whole-line association.
    Inputs and outputs describe only this evaluation, not a stored decision.
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
    for index, link in links.items():
        if type(index) is not int or not 0 <= index < len(invoice.lines):
            raise ValueError("Invoice line index is outside evaluation context")
        if link is not None and not isinstance(link, LineAssociation):
            raise ValueError("Association must be a LineAssociation or None")
    uses = Counter(link for link in links.values() if link is not None)
    findings = [rules.check_po_references(invoice), *check_policy_facts(invoice, policies)]
    for reference in invoice.po_references:
        po = orders.get(reference)
        findings.append(rules.check_po_exists(reference, po))
        if po is not None:
            findings.extend((rules.check_supplier(resolved_supplier_id, po),
                             rules.check_invoice_date(invoice, po)))

    for index, line in enumerate(invoice.lines):
        if index not in links:
            # TODO: multi-PO/split-line allocation policy. Raw text is not parsed.
            findings.append(RuleResult("LINE_ASSOCIATION", "PENDING",
                                       "Invoice line association has not been resolved.", line_index=index))
            continue
        link = links[index]
        if link is None:
            findings.append(RuleResult("LINE_ASSOCIATION", "HOLD",
                                       "Invoice line has no corresponding PO line.", line_index=index))
            continue
        po = orders.get(link.po_number)
        po_line = next((item for item in po.lines if item.line_number == link.po_line_number), None) if po else None
        if link.po_number not in invoice.po_references or po_line is None:
            findings.append(RuleResult("LINE_ASSOCIATION", "PENDING",
                                       "Association does not resolve to a referenced PO-local line.",
                                       link.po_number, index, link.po_line_number))
            continue
        findings.append(RuleResult("LINE_ASSOCIATION", "PASS", "Caller supplied a reliable PO-line association.",
                                   link.po_number, index, link.po_line_number))
        if uses[link] > 1:
            quantity_pending = "Multiple invoice lines share a PO line; receipt allocation policy is pending."
        elif not policies.quantities_comparable:
            # TODO: partial invoicing/prior invoice quantities. Never subtract
            # guessed previous consumption from the provided GRN sum.
            quantity_pending = "Partial-invoicing quantity basis has not been established."
        else:
            quantity_pending = None
        if quantity_pending is not None:
            findings.append(RuleResult("QUANTITY", "PENDING", quantity_pending,
                                       link.po_number, index, link.po_line_number))
        else:
            findings.append(rules.check_quantity(line, po, po_line, grns, index))
        if invoice.currency != po.currency:
            # TODO: cross-currency policy; no implicit conversion or discrepancy.
            price_pending = "Currencies differ; no conversion policy is implemented."
        elif not policies.unit_prices_comparable:
            # TODO: tax basis policy; a caller must establish comparability.
            price_pending = "Comparable unit-price tax basis has not been established."
        else:
            price_pending = None
        if price_pending is not None:
            findings.append(RuleResult("UNIT_PRICE", "PENDING", price_pending,
                                       link.po_number, index, link.po_line_number))
        else:
            findings.append(rules.check_price(line, po, po_line, config, index))

    # TODO: correction/later-GRN/parameter-change reevaluation and human decision
    # history belong to future workflows, not implicit mutation of this result.
    provisional: BusinessStatus = "Auto-Pay"
    if any(result.outcome == "REJECT" for result in findings):
        provisional = "Rejected"
    elif any(result.outcome == "HOLD" for result in findings):
        provisional = "Held for Review"
    complete = not any(result.outcome == "PENDING" for result in findings)
    return EvaluationResult(provisional, provisional if complete else None, tuple(findings), config)
