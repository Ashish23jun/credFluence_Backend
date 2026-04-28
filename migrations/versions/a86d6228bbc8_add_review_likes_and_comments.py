"""add_review_likes_and_comments

Revision ID: a86d6228bbc8
Revises: g7h8i9j0k1l2
Create Date: 2026-04-28 07:11:27.106024

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a86d6228bbc8'
down_revision: Union[str, None] = 'g7h8i9j0k1l2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('review_comments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('review_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active', 'removed', 'flagged')", name='ck_review_comments_status'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_comments_author_id'), 'review_comments', ['author_id'], unique=False)
    op.create_index(op.f('ix_review_comments_created_at'), 'review_comments', ['created_at'], unique=False)
    op.create_index(op.f('ix_review_comments_review_id'), 'review_comments', ['review_id'], unique=False)
    op.create_table('review_likes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('review_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('review_id', 'user_id', name='uq_review_likes_review_user')
    )
    op.create_index(op.f('ix_review_likes_review_id'), 'review_likes', ['review_id'], unique=False)
    op.create_index(op.f('ix_review_likes_user_id'), 'review_likes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_review_likes_user_id'), table_name='review_likes')
    op.drop_index(op.f('ix_review_likes_review_id'), table_name='review_likes')
    op.drop_table('review_likes')
    op.drop_index(op.f('ix_review_comments_review_id'), table_name='review_comments')
    op.drop_index(op.f('ix_review_comments_created_at'), table_name='review_comments')
    op.drop_index(op.f('ix_review_comments_author_id'), table_name='review_comments')
    op.drop_table('review_comments')
