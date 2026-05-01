"""add recipient_dispute type

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-04-30
"""
from alembic import op

revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE dispute_type ADD VALUE IF NOT EXISTS 'recipient_dispute'")


def downgrade() -> None:
    # Postgres does not support removing enum values
    pass
