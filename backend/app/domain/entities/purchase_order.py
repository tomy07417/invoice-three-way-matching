"""Purchase orders and their PO-local numbered lines."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class PurchaseOrderLine:
    line_number: int
    item_code: str
    description: str
    quantity_ordered: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    supplier_id: str
    po_date: date
    currency: str
    status: str
    lines: tuple[PurchaseOrderLine, ...]
