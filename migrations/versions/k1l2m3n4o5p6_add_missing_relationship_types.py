"""add missing relationship types to reviews enum

Revision ID: k1l2m3n4o5p6
Revises: 4173c1477241
Create Date: 2026-04-29
"""
from alembic import op

revision = 'k1l2m3n4o5p6'
down_revision = '4173c1477241'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL allows adding values to an existing ENUM without recreating it
    op.execute("ALTER TYPE relationship_type ADD VALUE IF NOT EXISTS 'brand_worked_with_agency'")
    op.execute("ALTER TYPE relationship_type ADD VALUE IF NOT EXISTS 'agency_worked_with_brand'")
    op.execute("ALTER TYPE relationship_type ADD VALUE IF NOT EXISTS 'agency_worked_with_agency'")
    op.execute("ALTER TYPE relationship_type ADD VALUE IF NOT EXISTS 'creator_worked_with_creator'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without a full type rebuild.
    # Intentionally left as no-op — safe to leave extra values in the type.
    pass
