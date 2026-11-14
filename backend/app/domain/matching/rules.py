"""Pure checks. Only the engine assigns business status and precedence."""
from decimal import Decimal, localcontext

from ..entities import (
    GoodsReceivedNote, Invoice, InvoiceLine, PurchaseOrder, PurchaseOrderLine,
)
from .types import MatchingConfig, RuleResult


def check_po_references(invoice: Invoice) -> RuleResult:
    if not invoice.po_references:
        return RuleResult("PO_REFERENCES", "REJECT", "Invoice references no PO.")
    return RuleResult("PO_REFERENCES", "PASS", "Invoice references at least one PO.")


def check_po_exists(po_number: str, po: PurchaseOrder | None) -> RuleResult:
    return RuleResult("PO_EXISTS", "PASS" if po is not None else "REJECT",
                      "Referenced PO exists." if po is not None else "Referenced PO does not exist.",
                      po_number)


def check_supplier(resolved_supplier_id: str | None, po: PurchaseOrder) -> RuleResult:
    if resolved_supplier_id is None:
        return RuleResult("SUPPLIER", "PENDING", "Invoice supplier identity has not been resolved.", po.po_number)
    if resolved_supplier_id != po.supplier_id:
        return RuleResult("SUPPLIER", "REJECT", "Resolved supplier differs from PO supplier.", po.po_number)
    return RuleResult("SUPPLIER", "PASS", "Resolved supplier matches PO supplier.", po.po_number)


def check_invoice_date(invoice: Invoice, po: PurchaseOrder) -> RuleResult:
    if invoice.invoice_date < po.po_date:
        return RuleResult("INVOICE_DATE", "REJECT", "Invoice date precedes PO date.", po.po_number)
    return RuleResult("INVOICE_DATE", "PASS", "Invoice date is on or after PO date.", po.po_number)


def _exact_precision(values: tuple[Decimal, ...]) -> int:
    # Include exponent gaps and product digits: never round at a tolerance boundary
    # merely because the caller uses a small ambient Decimal precision.
    return 10 + sum(len(value.as_tuple().digits) + abs(value.as_tuple().exponent)
                    for value in values)


def check_quantity(line: InvoiceLine, po: PurchaseOrder, po_line: PurchaseOrderLine,
                   grns: tuple[GoodsReceivedNote, ...], line_index: int) -> RuleResult:
    quantities = tuple(receipt.quantity_received for note in grns if note.po_number == po.po_number
                       for receipt in note.lines if receipt.po_line_number == po_line.line_number)
    if any(not value.is_finite() for value in (*quantities, line.quantity)):
        return RuleResult("QUANTITY", "PENDING", "Quantity comparison requires finite values.",
                          po.po_number, line_index, po_line.line_number)
    with localcontext() as context:
        context.prec = _exact_precision((*quantities, line.quantity))
        received = sum(quantities, Decimal("0"))
        exceeds = line.quantity > received
    return RuleResult("QUANTITY", "HOLD" if exceeds else "PASS",
                      "Invoiced quantity exceeds received quantity." if exceeds
                      else "Invoiced quantity is covered by receipts.",
                      po.po_number, line_index, po_line.line_number)


def check_price(line: InvoiceLine, po: PurchaseOrder, po_line: PurchaseOrderLine,
                config: MatchingConfig, line_index: int) -> RuleResult:
    values = (line.unit_price, po_line.unit_price, config.price_tolerance)
    if any(not value.is_finite() for value in values):
        return RuleResult("UNIT_PRICE", "PENDING", "Price comparison requires finite values.",
                          po.po_number, line_index, po_line.line_number)
    if po_line.unit_price <= 0:
        # TODO: zero-price/credit policy; do not invent a percentage denominator.
        return RuleResult("UNIT_PRICE", "PENDING", "Nonpositive PO price needs a percentage-basis policy.",
                          po.po_number, line_index, po_line.line_number)
    with localcontext() as context:
        context.prec = _exact_precision(values)
        exceeds = abs(line.unit_price - po_line.unit_price) > po_line.unit_price * config.price_tolerance
    return RuleResult("UNIT_PRICE", "HOLD" if exceeds else "PASS",
                      "Unit price difference exceeds tolerance." if exceeds
                      else "Unit price difference is within tolerance.",
                      po.po_number, line_index, po_line.line_number)
