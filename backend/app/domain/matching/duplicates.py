"""Pure duplicate and similarity checks on caller-supplied invoice history."""
from datetime import date
from decimal import Decimal

from ..entities import Invoice
from .types import InvoiceHistoryEntry, RuleResult


def normalize_invoice_number(number: str) -> str:
    """Ignore whitespace, ASCII hyphens and case only; never mutate originals.

    An empty result is unusable evidence, not a comparable invoice identifier.
    No punctuation removal, Unicode normalization or numeric coercion is applied.
    """
    return "".join(character for character in number
                   if not character.isspace() and character != "-").casefold()


def _pending(reason: str) -> tuple[RuleResult, ...]:
    return tuple(RuleResult(code, "PENDING", reason)
                 for code in ("DUPLICATE", "SIMILAR_INVOICE"))


def _usable_header(record: Invoice | InvoiceHistoryEntry) -> bool:
    return (isinstance(record.currency, str) and bool(record.currency.strip())
            and isinstance(record.invoice_date, date)
            and isinstance(record.total_amount, Decimal)
            and record.total_amount.is_finite())


def check_duplicate_history(
    invoice: Invoice,
    supplier_id: str | None,
    history: tuple[InvoiceHistoryEntry, ...] | None,
) -> tuple[RuleResult, ...]:
    """Return independent DUPLICATE and SIMILAR_INVOICE findings, in that order.

    Application supplies resolved supplier identity and authoritative history for
    other cases, excluding this case's own versions. None means missing history;
    an empty tuple means no history. This rule performs no storage/file access,
    case/version filtering, approval, or transactional payment deduplication.
    The caller combines findings and applies business-status precedence.
    """
    if supplier_id is None or not supplier_id.strip():
        return _pending("Invoice supplier identity has not been resolved.")
    if history is None:
        return _pending("Invoice history has not been supplied.")
    number = normalize_invoice_number(invoice.invoice_number)
    if not number:
        return _pending("Invoice number is unusable after normalization.")

    duplicate = False
    similar = False
    duplicate_pending = False
    similarity_pending = False
    for previous in history:
        if previous.supplier_id != supplier_id:
            continue
        previous_number = normalize_invoice_number(previous.invoice_number)
        if not previous_number:
            duplicate_pending = True
            similarity_pending = True
        elif previous_number == number:
            # Identity alone establishes rejection, regardless of other fields.
            duplicate = True
        elif not _usable_header(invoice) or not _usable_header(previous):
            similarity_pending = True
        elif (previous.currency == invoice.currency
              and previous.total_amount == invoice.total_amount
              and previous.invoice_date == invoice.invoice_date):
            similar = True

    if duplicate:
        duplicate_result = RuleResult(
            "DUPLICATE", "REJECT",
            "Same supplier and normalized invoice number found in history.")
    elif duplicate_pending:
        duplicate_result = RuleResult(
            "DUPLICATE", "PENDING",
            "Duplicate comparison requires usable invoice numbers in history.")
    else:
        duplicate_result = RuleResult(
            "DUPLICATE", "PASS",
            "No same-supplier normalized invoice number found in history.")

    if similar:
        similarity_result = RuleResult(
            "SIMILAR_INVOICE", "HOLD",
            "Different invoice number with identical total and date found for the same supplier and currency.")
    elif similarity_pending:
        similarity_result = RuleResult(
            "SIMILAR_INVOICE", "PENDING",
            "Similarity comparison requires usable invoice numbers, currency, date and finite totals.")
    else:
        similarity_result = RuleResult(
            "SIMILAR_INVOICE", "PASS",
            "No different-number same-supplier same-currency invoice with identical total and date found in history.")
    return duplicate_result, similarity_result
