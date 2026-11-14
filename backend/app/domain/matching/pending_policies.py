"""Unresolved policies stay visible without stopping available checks."""
from ..entities import Invoice
from .types import PolicyFacts, RuleResult


def check_duplicate(invoice: Invoice) -> bool | None:
    # TODO: owner must define duplicates/resubmissions. Never infer from invoice
    # number, supplier tax ID, totals or processing history. None means PENDING.
    return None


def check_policy_facts(invoice: Invoice, facts: PolicyFacts) -> tuple[RuleResult, ...]:
    duplicate = facts.duplicate if facts.duplicate is not None else check_duplicate(invoice)
    duplicate_result = RuleResult(
        "DUPLICATE", "PENDING" if duplicate is None else "REJECT" if duplicate else "PASS",
        "Duplicate definition is pending; no duplicate algorithm is implemented." if duplicate is None
        else "Caller verified that this invoice is a duplicate." if duplicate
        else "Caller verified that this invoice is not a duplicate.",
    )
    # TODO: inactive-supplier and closed-PO policies. A caller may supply verified
    # clearance for this snapshot; no status-string inference is made here.
    return (
        duplicate_result,
        RuleResult("SUPPLIER_ACTIVITY", "PASS" if facts.supplier_activity_cleared else "PENDING",
                   "Caller verified supplier-activity policy clearance." if facts.supplier_activity_cleared
                   else "Supplier-activity policy has not been resolved."),
        RuleResult("PO_STATUS", "PASS" if facts.po_status_cleared else "PENDING",
                   "Caller verified PO-status policy clearance." if facts.po_status_cleared
                   else "PO-status policy has not been resolved."),
    )
