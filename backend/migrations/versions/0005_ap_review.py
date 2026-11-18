"""Versioned AP review, audited decisions and compensating approval releases.

Revision ID: 0005
Revises: 0004
Existing raw invoices, evaluations, processing and approval facts are untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('review_cases',
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('upload_cases.invoice_id'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('extraction_version', sa.Integer(), nullable=False),
        sa.Column('evaluation_id', sa.Integer(), sa.ForeignKey('invoice_evaluations.id')),
        sa.Column('current_decision_id', sa.Integer()),
        sa.Column('operational_state', sa.String(), nullable=False),
        sa.Column('payment_blocked', sa.Boolean(), nullable=False),
        sa.Column('credit_note_pending', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('revision >= 0 AND extraction_version >= 1', name='ck_review_revision'))
    op.create_table('review_extraction_versions',
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('review_cases.invoice_id'), primary_key=True),
        sa.Column('number', sa.Integer(), primary_key=True),
        sa.Column('draft', JSONB()),
        sa.Column('raw_invoice_id', sa.Integer(), sa.ForeignKey('invoices.id')),
        sa.Column('actor_id', sa.String(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comment', sa.String(), nullable=False),
        sa.CheckConstraint('number >= 1', name='ck_review_extraction_number'))
    op.create_index('ix_review_extraction_versions_raw_invoice_id', 'review_extraction_versions', ['raw_invoice_id'])
    op.create_table('review_evaluation_links',
        sa.Column('evaluation_id', sa.Integer(), sa.ForeignKey('invoice_evaluations.id'), primary_key=True),
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('review_cases.invoice_id'), nullable=False),
        sa.Column('extraction_version', sa.Integer(), nullable=False))
    op.create_index('ix_review_evaluation_links_invoice_id', 'review_evaluation_links', ['invoice_id'])
    op.create_table('review_decisions',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('review_cases.invoice_id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('extraction_version', sa.Integer(), nullable=False),
        sa.Column('evaluation_id', sa.Integer(), sa.ForeignKey('invoice_evaluations.id')),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comment', sa.String(), nullable=False),
        sa.UniqueConstraint('invoice_id', 'revision', name='uq_review_decision_revision'))
    op.create_index('ix_review_decisions_invoice_id', 'review_decisions', ['invoice_id'])
    op.create_table('review_approval_links',
        sa.Column('ledger_id', sa.Integer(), sa.ForeignKey('approved_payment_quantities.id'), primary_key=True),
        sa.Column('decision_id', sa.Integer(), sa.ForeignKey('review_decisions.id'), nullable=False))
    op.create_index('ix_review_approval_links_decision_id', 'review_approval_links', ['decision_id'])
    op.create_table('review_approval_reversals',
        sa.Column('decision_id', sa.Integer(), sa.ForeignKey('review_decisions.id'), primary_key=True),
        sa.Column('actor_id', sa.String(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comment', sa.String(), nullable=False))
    op.create_table('review_audit_events',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('review_cases.invoice_id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=False),
        sa.Column('actor_name', sa.String(), nullable=False),
        sa.Column('actor_role', sa.String(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('comment', sa.String(), nullable=False),
        sa.Column('payload', JSONB(), nullable=False))
    op.create_index('ix_review_audit_events_invoice_id', 'review_audit_events', ['invoice_id'])


def downgrade():
    for table in ('review_audit_events', 'review_approval_reversals', 'review_approval_links',
                  'review_decisions', 'review_evaluation_links', 'review_extraction_versions', 'review_cases'):
        op.drop_table(table)
