"""Retrieve received totals without making invoice or payment decisions."""
from app.application.dtos.purchase_orders import PurchaseOrderView
from app.application.ports.purchase_orders import PurchaseOrderRepository


def get_purchase_order(po_number: str, repository: PurchaseOrderRepository) -> PurchaseOrderView | None:
    return repository.get(po_number)
