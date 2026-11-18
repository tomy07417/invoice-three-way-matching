"""Append-only Manager parameter evidence; preserve all historical records.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('parameter_changes',
        sa.Column('version_id', sa.Integer(), sa.ForeignKey('rule_configuration_versions.id'), primary_key=True),
        sa.Column('previous_version_id', sa.Integer(), sa.ForeignKey('rule_configuration_versions.id'), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('actor_username', sa.String(), nullable=False),
        sa.Column('actor_role', sa.String(), nullable=False),
        sa.Column('comment', sa.String()))
    # No backfill: bootstrap/legacy actor identity and change evidence are unknown.


def downgrade():
    op.drop_table('parameter_changes')
