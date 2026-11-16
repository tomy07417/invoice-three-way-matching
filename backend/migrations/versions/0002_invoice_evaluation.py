"""Add append-only evaluation evidence and explicit approval facts.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoices", sa.Column("resolved_supplier_id", sa.String(), nullable=True))
    op.create_foreign_key("fk_invoice_resolved_supplier", "invoices", "suppliers", ["resolved_supplier_id"], ["supplier_id"])
    op.add_column("invoices", sa.Column("operational_state", sa.String(), nullable=False, server_default="received"))
    op.create_check_constraint("ck_invoice_operational_state", "invoices", "operational_state IN ('received', 'evaluated', 'evaluation_incomplete')")
    op.create_table(
        "rule_configuration_versions",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_tolerance", sa.Numeric(), nullable=False),
        sa.Column("analyst_approval_limit", sa.Numeric(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.CheckConstraint("price_tolerance >= 0 AND analyst_approval_limit >= 0", name="ck_configuration_nonnegative"),
    )
    op.execute("INSERT INTO rule_configuration_versions (effective_from, price_tolerance, analyst_approval_limit, actor_id) VALUES ('1970-01-01 00:00:00+00', 0.02, 5000, 'bootstrap')")
    # Intentionally empty: only explicit approvals consume received quantities.
    op.create_table(
        "approved_payment_quantities",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("po_number", sa.String(), nullable=False),
        sa.Column("po_line_number", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["po_number", "po_line_number"], ["purchase_order_lines.po_number", "purchase_order_lines.line_number"], name="fk_approval_po_line"),
    )
    op.create_table(
        "invoice_evaluations",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("configuration_id", sa.Integer(), sa.ForeignKey("rule_configuration_versions.id"), nullable=False),
        sa.Column("price_tolerance", sa.Numeric(), nullable=False),
        sa.Column("analyst_approval_limit", sa.Numeric(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("provisional_status", sa.String(), nullable=False),
        sa.Column("final_status", sa.String(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("input_snapshot", JSONB(), nullable=False),
        sa.Column("context_snapshot", JSONB(), nullable=False),
        sa.CheckConstraint("provisional_status IN ('Auto-Pay', 'Rejected', 'Held for Review')", name="ck_evaluation_provisional"),
        sa.CheckConstraint("final_status IS NULL OR final_status IN ('Auto-Pay', 'Rejected', 'Held for Review')", name="ck_evaluation_final"),
        sa.CheckConstraint("(complete AND final_status IS NOT NULL AND final_status = provisional_status) OR (NOT complete AND final_status IS NULL)", name="ck_evaluation_completeness"),
    )
    op.create_table(
        "evaluation_rule_findings",
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("invoice_evaluations.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("po_number", sa.String(), nullable=True),
        sa.Column("line_index", sa.Integer(), nullable=True),
        sa.Column("po_line_number", sa.Integer(), nullable=True),
        sa.CheckConstraint("outcome IN ('PASS', 'HOLD', 'REJECT', 'PENDING')", name="ck_finding_outcome"),
    )


def downgrade():
    for name in ("evaluation_rule_findings", "invoice_evaluations", "approved_payment_quantities", "rule_configuration_versions"):
        op.drop_table(name)
    op.drop_constraint("ck_invoice_operational_state", "invoices", type_="check")
    op.drop_column("invoices", "operational_state")
    op.drop_constraint("fk_invoice_resolved_supplier", "invoices", type_="foreignkey")
    op.drop_column("invoices", "resolved_supplier_id")
