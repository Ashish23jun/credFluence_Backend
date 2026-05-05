"""add score_history table

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'score_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('profile_id', UUID(as_uuid=True), sa.ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('review_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reason', sa.String(64), nullable=False, server_default='review_verified'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_score_history_profile_id', 'score_history', ['profile_id'])


def downgrade() -> None:
    op.drop_index('ix_score_history_profile_id', table_name='score_history')
    op.drop_table('score_history')
