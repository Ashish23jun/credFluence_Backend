"""add new_comment and review_liked to notification enums

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-05-07
"""
import sqlalchemy as sa
from alembic import op

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'new_comment'"))
    op.execute(sa.text("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'review_liked'"))
    op.execute(sa.text("ALTER TYPE notif_pref_type ADD VALUE IF NOT EXISTS 'new_comment'"))
    op.execute(sa.text("ALTER TYPE notif_pref_type ADD VALUE IF NOT EXISTS 'review_liked'"))


def downgrade() -> None:
    pass
