"""Synchronous reference-data adapter using the existing SQL records."""
from contextlib import contextmanager
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.reference_data import ReferenceData, ReferenceDataError
from app.domain import entities
from app.infrastructure.persistence import models


def column_values(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class ReferenceDataTransaction:
    def __init__(self, session: Session):
        self.session = session

    def load(self) -> ReferenceData:
        suppliers = tuple(entities.Supplier(**column_values(row))
                          for row in self.session.scalars(select(models.Supplier)))
        po_lines = {}
        for row in self.session.scalars(select(models.PurchaseOrderLine)):
            values = column_values(row)
            parent = values.pop("po_number")
            po_lines.setdefault(parent, []).append(entities.PurchaseOrderLine(**values))
        orders = tuple(entities.PurchaseOrder(**column_values(row),
                                             lines=tuple(po_lines.get(row.po_number, ())))
                       for row in self.session.scalars(select(models.PurchaseOrder)))
        grn_lines = {}
        for row in self.session.scalars(select(models.GoodsReceivedNoteLine)):
            grn_lines.setdefault(row.grn_number, []).append(entities.GoodsReceivedNoteLine(
                po_line_number=row.po_line_number, quantity_received=row.quantity_received))
        receipts = tuple(entities.GoodsReceivedNote(**column_values(row),
                                                   lines=tuple(grn_lines.get(row.grn_number, ())))
                         for row in self.session.scalars(select(models.GoodsReceivedNote)))
        return ReferenceData(suppliers, orders, receipts)

    def add(self, data: ReferenceData) -> None:
        self.session.add_all(models.Supplier(**asdict(supplier)) for supplier in data.suppliers)
        self.session.flush()
        for po in data.purchase_orders:
            values = asdict(po)
            values.pop("lines")
            self.session.add(models.PurchaseOrder(**values))
        self.session.flush()
        for po in data.purchase_orders:
            self.session.add_all(models.PurchaseOrderLine(po_number=po.po_number, **asdict(line))
                                 for line in po.lines)
        self.session.flush()
        for grn in data.goods_received_notes:
            values = asdict(grn)
            values.pop("lines")
            self.session.add(models.GoodsReceivedNote(**values))
        self.session.flush()
        for grn in data.goods_received_notes:
            self.session.add_all(models.GoodsReceivedNoteLine(
                grn_number=grn.grn_number, po_number=grn.po_number, **asdict(line))
                for line in grn.lines)
        self.session.flush()


class SqlReferenceDataRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def transaction(self):
        try:
            with Session(self.engine) as session, session.begin():
                yield ReferenceDataTransaction(session)
        except IntegrityError:
            raise ReferenceDataError("database integrity conflict; no records imported") from None
        except SQLAlchemyError:
            raise ReferenceDataError("database operation failed; check configuration and migrations") from None
