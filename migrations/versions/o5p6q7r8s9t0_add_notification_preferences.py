"""add notification_preferences table

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE notif_pref_channel AS ENUM ('email', 'in_app')
    """)
    op.execute("""
        CREATE TYPE notif_pref_type AS ENUM (
            'review_received', 'dispute_filed', 'dispute_resolved',
            'review_verified', 'review_rejected', 'profile_claimed',
            'score_updated', 'badge_earned'
        )
    """)
    op.create_table(
        "notification_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Enum(name="notif_pref_channel", create_type=False), nullable=False),
        sa.Column("type", sa.Enum(name="notif_pref_type", create_type=False), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_unique_constraint(
        "uq_notif_pref", "notification_preferences", ["user_id", "channel", "type"]
    )
    op.create_index(
        "ix_notif_pref_lookup", "notification_preferences", ["user_id", "channel", "type"]
    )


def downgrade() -> None:
    op.drop_index("ix_notif_pref_lookup", table_name="notification_preferences")
    op.drop_constraint("uq_notif_pref", "notification_preferences", type_="unique")
    op.drop_table("notification_preferences")
    op.execute("DROP TYPE notif_pref_channel")
    op.execute("DROP TYPE notif_pref_type")
