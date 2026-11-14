"""Minimal read-only reference query boundary."""
from typing import Protocol

from app.application.dtos.purchase_orders import PurchaseOrderView


class PurchaseOrderRepository(Protocol):
    def get(self, po_number: str) -> PurchaseOrderView | None: ...
