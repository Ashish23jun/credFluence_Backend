"""add niches and languages to profiles

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-04-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'e5f6a7b8c9d0'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('profiles', sa.Column('niches', JSONB(), nullable=True))
    op.add_column('profiles', sa.Column('languages', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('profiles', 'languages')
    op.drop_column('profiles', 'niches')
