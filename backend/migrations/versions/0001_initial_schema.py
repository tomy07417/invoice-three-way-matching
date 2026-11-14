"""Create reference data and raw invoice tables.

Revision ID: 0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("supplier_id", sa.String(), primary_key=True),
        sa.Column("legal_name", sa.String(), nullable=False),
        sa.Column("tax_id", sa.String(), nullable=False),
        sa.Column("default_currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("po_number", sa.String(), primary_key=True),
        sa.Column("supplier_id", sa.String(), sa.ForeignKey("suppliers.supplier_id"), nullable=False),
        sa.Column("po_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
    )
    op.create_table(
        "purchase_order_lines",
        sa.Column("po_number", sa.String(), sa.ForeignKey("purchase_orders.po_number"), primary_key=True),
        sa.Column("line_number", sa.Integer(), primary_key=True),
        sa.Column("item_code", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity_ordered", sa.Numeric(), nullable=False),
        sa.Column("unit_price", sa.Numeric(), nullable=False),
    )
    op.create_table(
        "goods_received_notes",
        sa.Column("grn_number", sa.String(), primary_key=True),
        sa.Column("po_number", sa.String(), sa.ForeignKey("purchase_orders.po_number"), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.UniqueConstraint("grn_number", "po_number", name="uq_grn_po"),
    )
    op.create_table(
        "goods_received_note_lines",
        sa.Column("grn_number", sa.String(), primary_key=True),
        sa.Column("po_line_number", sa.Integer(), primary_key=True),
        sa.Column("po_number", sa.String(), nullable=False),
        sa.Column("quantity_received", sa.Numeric(), nullable=False),
        sa.ForeignKeyConstraint(
            ["grn_number", "po_number"],
            ["goods_received_notes.grn_number", "goods_received_notes.po_number"],
            name="fk_grn_line_header_po",
        ),
        sa.ForeignKeyConstraint(
            ["po_number", "po_line_number"],
            ["purchase_order_lines.po_number", "purchase_order_lines.line_number"],
            name="fk_grn_line_po_line",
        ),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("supplier_name", sa.String(), nullable=False),
        sa.Column("supplier_tax_id", sa.String(), nullable=False),
        sa.Column("invoice_number", sa.String(), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("subtotal", sa.Numeric(), nullable=False),
        sa.Column("tax_amount", sa.Numeric(), nullable=False),
        sa.Column("total_amount", sa.Numeric(), nullable=False),
    )
    op.create_table(
        "invoice_lines",
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), primary_key=True),
        sa.Column("line_number", sa.Integer(), primary_key=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("unit_price", sa.Numeric(), nullable=False),
        sa.Column("line_total", sa.Numeric(), nullable=False),
        sa.Column("po_line_reference", sa.String(), nullable=True),
    )
    op.create_table(
        "invoice_po_references",
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("po_number", sa.String(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "invoice_po_references", "invoice_lines", "invoices",
        "goods_received_note_lines", "goods_received_notes",
        "purchase_order_lines", "purchase_orders", "suppliers",
    ):
        op.drop_table(table)
