"""Read-only PO view; exact quantities remain Decimal until the HTTP boundary."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class PurchaseOrderUnavailable(Exception):
    """Safe read failure, independent of the database driver."""


@dataclass(frozen=True)
class PurchaseOrderLineView:
    line_number: int
    item_code: str
    description: str
    quantity_ordered: Decimal
    unit_price: Decimal
    quantity_received: Decimal


@dataclass(frozen=True)
class PurchaseOrderView:
    po_number: str
    supplier_id: str
    po_date: date
    currency: str
    status: str
    lines: tuple[PurchaseOrderLineView, ...]
