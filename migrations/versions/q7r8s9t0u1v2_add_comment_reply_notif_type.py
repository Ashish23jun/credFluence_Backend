"""add comment_reply to notification enums

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction block (not rollback-able
    # but safe to commit); IF NOT EXISTS makes reruns idempotent.
    op.execute(sa.text("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'comment_reply'"))
    op.execute(sa.text("ALTER TYPE notif_pref_type ADD VALUE IF NOT EXISTS 'comment_reply'"))


def downgrade() -> None:
    # Postgres does not support removing enum values — downgrade is a no-op.
    pass
