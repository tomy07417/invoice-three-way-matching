"""SQL records independent of domain entities and payment policy.

Unbounded NUMERIC preserves Decimal values. Invoice references are raw extracted
text, deliberately not foreign keys to reference data that may not exist.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Identity, Numeric, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"

    supplier_id: Mapped[str] = mapped_column(primary_key=True)
    legal_name: Mapped[str]
    tax_id: Mapped[str]
    default_currency: Mapped[str]
    status: Mapped[str]
    address: Mapped[str]


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    po_number: Mapped[str] = mapped_column(primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.supplier_id"))
    po_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str]
    status: Mapped[str]


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    po_number: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_number"), primary_key=True)
    line_number: Mapped[int] = mapped_column(primary_key=True)
    item_code: Mapped[str]
    description: Mapped[str]
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric())
    unit_price: Mapped[Decimal] = mapped_column(Numeric())


class GoodsReceivedNote(Base):
    __tablename__ = "goods_received_notes"
    __table_args__ = (UniqueConstraint("grn_number", "po_number", name="uq_grn_po"),)

    grn_number: Mapped[str] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_number"))
    received_date: Mapped[date] = mapped_column(Date)


class GoodsReceivedNoteLine(Base):
    __tablename__ = "goods_received_note_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["grn_number", "po_number"],
            ["goods_received_notes.grn_number", "goods_received_notes.po_number"],
            name="fk_grn_line_header_po",
        ),
        ForeignKeyConstraint(
            ["po_number", "po_line_number"],
            ["purchase_order_lines.po_number", "purchase_order_lines.line_number"],
            name="fk_grn_line_po_line",
        ),
    )

    grn_number: Mapped[str] = mapped_column(primary_key=True)
    po_line_number: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str]
    quantity_received: Mapped[Decimal] = mapped_column(Numeric())


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint(
        "operational_state IN ('received', 'evaluated', 'evaluation_incomplete')",
        name="ck_invoice_operational_state"),)

    resolved_supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.supplier_id"))
    operational_state: Mapped[str] = mapped_column(server_default="received")
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    supplier_name: Mapped[str]
    supplier_tax_id: Mapped[str]
    invoice_number: Mapped[str]
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str]
    subtotal: Mapped[Decimal] = mapped_column(Numeric())
    tax_amount: Mapped[Decimal] = mapped_column(Numeric())
    total_amount: Mapped[Decimal] = mapped_column(Numeric())


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), primary_key=True)
    line_number: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str]
    quantity: Mapped[Decimal] = mapped_column(Numeric())
    unit_price: Mapped[Decimal] = mapped_column(Numeric())
    line_total: Mapped[Decimal] = mapped_column(Numeric())
    po_line_reference: Mapped[str | None]


class InvoicePOReference(Base):
    __tablename__ = "invoice_po_references"

    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str]


# Register evaluation tables for shared metadata consumers (including Alembic).
from app.infrastructure.persistence import evaluation_records  # noqa: E402,F401
