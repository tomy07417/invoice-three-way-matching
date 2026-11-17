"""Composition root: existing routes remain usable without DB or Azure settings."""
import os

from fastapi import FastAPI

from app.application.use_cases.get_purchase_order import get_purchase_order
from app.infrastructure.persistence.purchase_order_repository import SqlPurchaseOrderRepository
from app.interfaces.api.health import router as health_router
from app.interfaces.api.purchase_orders import create_router


def create_app(*, invoice_service=None, processing_enabled=None):
    """Enable with INVOICE_PROCESSING_ENABLED=1, or inject an InvoiceService.

    Injected services do not start a worker: tests/control tools explicitly call
    ProcessInvoice.process_once(). Production resources share application lifespan.
    """
    lifespan = None
    if processing_enabled is None:
        processing_enabled = os.environ.get('INVOICE_PROCESSING_ENABLED') == '1'
    if invoice_service is None and processing_enabled:
        from app.infrastructure.processing.composition import build_processing
        invoice_service, lifespan = build_processing()
    application = FastAPI(title='Invoice Three-Way Matching', lifespan=lifespan)
    application.include_router(health_router)
    purchase_orders = SqlPurchaseOrderRepository()
    application.include_router(create_router(lambda number: get_purchase_order(number, purchase_orders)))
    if invoice_service is not None:
        from app.interfaces.api.invoices import build_invoice_router
        from app.interfaces.api.sessions import build_session_router
        application.include_router(build_session_router(invoice_service.sessions,
                                                         secure_cookie=os.environ.get('SESSION_SECURE_COOKIE') == '1'))
        application.include_router(build_invoice_router(invoice_service))
    return application


app = create_app()
