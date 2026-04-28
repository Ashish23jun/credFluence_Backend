"""add contact_email and contact_phone to reviews

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-04-28
"""
import sqlalchemy as sa
from alembic import op

revision = 'j0k1l2m3n4o5'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reviews', sa.Column('contact_email', sa.String(254), nullable=True))
    op.add_column('reviews', sa.Column('contact_phone', sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column('reviews', 'contact_phone')
    op.drop_column('reviews', 'contact_email')
