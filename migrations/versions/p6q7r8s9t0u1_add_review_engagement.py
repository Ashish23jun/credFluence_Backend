"""add review engagement (replies, comment likes, nested comments)

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-05-06
"""
from alembic import op

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Official org reply — one per review, pinned as "Official Response"
    op.execute("""
        CREATE TABLE IF NOT EXISTS review_replies (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            review_id   UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
            org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            body        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_review_reply UNIQUE (review_id, org_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_replies_review_id ON review_replies (review_id)")

    # Comment likes — any user can like any comment, once
    op.execute("""
        CREATE TABLE IF NOT EXISTS comment_likes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            comment_id  UUID NOT NULL REFERENCES review_comments(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_comment_like UNIQUE (comment_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_comment_likes_comment_id ON comment_likes (comment_id)")

    # Nested comment replies — parent_comment_id on review_comments
    op.execute("""
        ALTER TABLE review_comments
        ADD COLUMN IF NOT EXISTS parent_comment_id UUID REFERENCES review_comments(id) ON DELETE CASCADE
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_comments_parent ON review_comments (parent_comment_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_review_comments_parent")
    op.execute("ALTER TABLE review_comments DROP COLUMN IF EXISTS parent_comment_id")
    op.execute("DROP INDEX IF EXISTS ix_comment_likes_comment_id")
    op.execute("DROP TABLE IF EXISTS comment_likes")
    op.execute("DROP INDEX IF EXISTS ix_review_replies_review_id")
    op.execute("DROP TABLE IF EXISTS review_replies")
