"""Extracted invoice values, before deterministic matching and evaluation."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    # Raw extraction text, not a globally unique PO-line foreign key.
    po_line_reference: str | None


@dataclass(frozen=True)
class Invoice:
    supplier_name: str
    supplier_tax_id: str
    invoice_number: str
    invoice_date: date
    due_date: date | None
    po_references: tuple[str, ...]
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    lines: tuple[InvoiceLine, ...]
