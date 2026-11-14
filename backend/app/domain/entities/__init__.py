"""Framework-independent domain records."""

from .goods_received_note import GoodsReceivedNote, GoodsReceivedNoteLine
from .invoice import Invoice, InvoiceLine
from .purchase_order import PurchaseOrder, PurchaseOrderLine
from .supplier import Supplier

__all__ = [
    "Supplier",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "GoodsReceivedNote",
    "GoodsReceivedNoteLine",
    "Invoice",
    "InvoiceLine",
]
