"""Composition root for the HTTP application."""

from fastapi import FastAPI

from app.application.use_cases.get_purchase_order import get_purchase_order
from app.infrastructure.persistence.purchase_order_repository import SqlPurchaseOrderRepository
from app.interfaces.api.health import router as health_router
from app.interfaces.api.purchase_orders import create_router

app = FastAPI(title="Invoice Three-Way Matching")
app.include_router(health_router)
# Constructing this adapter does not read configuration or construct an engine.
purchase_orders = SqlPurchaseOrderRepository()
app.include_router(create_router(lambda po_number: get_purchase_order(po_number, purchase_orders)))
