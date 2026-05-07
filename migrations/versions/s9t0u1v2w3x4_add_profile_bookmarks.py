"""add profile_bookmarks table

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_bookmarks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "profile_id", name="uq_bookmark_user_profile"),
    )
    op.create_index("ix_profile_bookmarks_user_id", "profile_bookmarks", ["user_id"])
    op.create_index("ix_profile_bookmarks_profile_id", "profile_bookmarks", ["profile_id"])


def downgrade() -> None:
    op.drop_table("profile_bookmarks")
