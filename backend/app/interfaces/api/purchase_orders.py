"""HTTP schemas and error mapping for read-only PO lookup."""
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.application.dtos.purchase_orders import PurchaseOrderUnavailable, PurchaseOrderView


class PurchaseOrderLineResponse(BaseModel):
    line_number: int
    item_code: str
    description: str
    quantity_ordered: Decimal
    unit_price: Decimal
    quantity_received: Decimal


class PurchaseOrderResponse(BaseModel):
    po_number: str
    supplier_id: str
    po_date: date
    currency: str
    status: str
    lines: list[PurchaseOrderLineResponse]


def create_router(query: Callable[[str], PurchaseOrderView | None]) -> APIRouter:
    router = APIRouter()

    @router.get("/purchase-orders/{po_number}", response_model=PurchaseOrderResponse,
                responses={404: {"description": "Purchase order not found"},
                           503: {"description": "Database unavailable"}})
    def purchase_order(po_number: str):
        try:
            result = query(po_number)
        except PurchaseOrderUnavailable:
            raise HTTPException(status_code=503, detail="Database unavailable") from None
        if result is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return result

    return router
