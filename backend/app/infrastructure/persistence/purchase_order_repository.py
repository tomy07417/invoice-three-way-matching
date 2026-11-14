"""Read-only SQLAlchemy adapter; configuration and connections are request-lazy."""
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.application.dtos.purchase_orders import (
    PurchaseOrderLineView, PurchaseOrderUnavailable, PurchaseOrderView,
)
from app.infrastructure.persistence.connection import create_database_engine
from app.infrastructure.persistence.models import GoodsReceivedNoteLine, PurchaseOrder, PurchaseOrderLine


class SqlPurchaseOrderRepository:
    def get(self, po_number: str) -> PurchaseOrderView | None:
        engine = None
        try:
            engine = create_database_engine()
            with engine.connect() as connection:
                header = connection.execute(select(PurchaseOrder).where(
                    PurchaseOrder.po_number == po_number,
                )).mappings().one_or_none()
                if header is None:
                    return None
                # Aggregate before joining; identities are PO-local, never item-code/global line.
                received = select(
                    GoodsReceivedNoteLine.po_number,
                    GoodsReceivedNoteLine.po_line_number,
                    func.sum(GoodsReceivedNoteLine.quantity_received).label("quantity_received"),
                ).where(GoodsReceivedNoteLine.po_number == po_number).group_by(
                    GoodsReceivedNoteLine.po_number, GoodsReceivedNoteLine.po_line_number,
                ).subquery()
                rows = connection.execute(select(
                    PurchaseOrderLine.line_number, PurchaseOrderLine.item_code,
                    PurchaseOrderLine.description, PurchaseOrderLine.quantity_ordered,
                    PurchaseOrderLine.unit_price,
                    func.coalesce(received.c.quantity_received, Decimal(0)).label("quantity_received"),
                ).outerjoin(received, and_(
                    received.c.po_number == PurchaseOrderLine.po_number,
                    received.c.po_line_number == PurchaseOrderLine.line_number,
                )).where(PurchaseOrderLine.po_number == po_number).order_by(
                    PurchaseOrderLine.line_number,
                )).mappings()
                return PurchaseOrderView(**header, lines=tuple(
                    PurchaseOrderLineView(**row) for row in rows
                ))
        except (SQLAlchemyError, ValueError, OSError):
            # Never expose driver exceptions, URLs, SQL, or bound parameters to callers.
            raise PurchaseOrderUnavailable("Database unavailable") from None
        finally:
            if engine is not None:
                engine.dispose()
