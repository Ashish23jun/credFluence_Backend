"""add member_request to notification enums

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-05-07
"""
import sqlalchemy as sa
from alembic import op

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'member_request'"))
    op.execute(sa.text("ALTER TYPE notif_pref_type ADD VALUE IF NOT EXISTS 'member_request'"))


def downgrade() -> None:
    pass
