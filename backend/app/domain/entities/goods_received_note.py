"""Receipt records; multiple GRNs may reference the same PO line."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class GoodsReceivedNoteLine:
    po_line_number: int
    quantity_received: Decimal


@dataclass(frozen=True)
class GoodsReceivedNote:
    grn_number: str
    po_number: str
    received_date: date
    lines: tuple[GoodsReceivedNoteLine, ...]
