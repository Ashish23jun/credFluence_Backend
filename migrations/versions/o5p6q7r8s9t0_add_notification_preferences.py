"""add notification_preferences table

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-06
"""
from alembic import op

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notif_pref_channel AS ENUM ('email', 'in_app');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notif_pref_type AS ENUM (
                'review_received', 'dispute_filed', 'dispute_resolved',
                'review_verified', 'review_rejected', 'profile_claimed',
                'score_updated', 'badge_earned'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id          UUID PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            channel     notif_pref_channel NOT NULL,
            type        notif_pref_type NOT NULL,
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            CONSTRAINT uq_notif_pref UNIQUE (user_id, channel, type)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_notif_pref_lookup
        ON notification_preferences (user_id, channel, type)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notif_pref_lookup")
    op.execute("DROP TABLE IF EXISTS notification_preferences")
    op.execute("DROP TYPE IF EXISTS notif_pref_channel")
    op.execute("DROP TYPE IF EXISTS notif_pref_type")
