"""Durable upload cases, fenced attempts, events and global admission lock.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('processing_control',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('configuration', JSONB()),
        sa.CheckConstraint('id = 1', name='ck_processing_single_control'))
    op.execute('INSERT INTO processing_control (id) VALUES (1)')
    op.create_table('upload_cases',
        sa.Column('invoice_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(), nullable=False),
        sa.Column('storage_key', sa.String(), nullable=False),
        sa.Column('document_metadata', JSONB(), nullable=False),
        sa.Column('estimated_tokens', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('deferrals', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('lease_token', UUID(as_uuid=True)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('next_eligible_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error_code', sa.String()),
        sa.Column('extraction', JSONB()),
        sa.Column('raw_invoice_id', sa.Integer(), sa.ForeignKey('invoices.id')),
        sa.Column('evaluation_id', sa.Integer(), sa.ForeignKey('invoice_evaluations.id')),
        sa.UniqueConstraint('owner_id', 'idempotency_key', name='uq_upload_owner_key'),
        sa.CheckConstraint("state IN ('QUEUED','EXTRACTING','EXTRACTED','EVALUATING','COMPLETED',"
                           "'RETRY_WAIT','EXTRACTION_FAILED','EVALUATION_INCOMPLETE','PROCESSING_FAILED')",
                           name='ck_processing_state'),
        sa.CheckConstraint('attempts >= 0 AND deferrals >= 0 AND version >= 0 AND estimated_tokens > 0',
                           name='ck_processing_counters'))
    for column in ('owner_id', 'state', 'next_eligible_at'):
        op.create_index('ix_upload_cases_' + column, 'upload_cases', [column])
    op.create_table('processing_attempts',
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('upload_cases.invoice_id'), primary_key=True),
        sa.Column('number', sa.Integer(), primary_key=True),
        sa.Column('token', UUID(as_uuid=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('reserved_tokens', sa.Integer(), nullable=False),
        sa.Column('reported_tokens', sa.Integer()),
        sa.Column('error_code', sa.String()),
        sa.CheckConstraint('reserved_tokens > 0 AND number > 0', name='ck_attempt_budget'))
    op.create_index('ix_processing_attempts_started_at', 'processing_attempts', ['started_at'])
    op.create_table('processing_events',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('invoice_id', UUID(as_uuid=True), sa.ForeignKey('upload_cases.invoice_id'), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('error_code', sa.String()))
    op.create_index('ix_processing_events_invoice_id', 'processing_events', ['invoice_id'])


def downgrade():
    op.drop_table('processing_events')
    op.drop_table('processing_attempts')
    op.drop_table('upload_cases')
    op.drop_table('processing_control')
