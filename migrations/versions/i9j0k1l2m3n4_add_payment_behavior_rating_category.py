"""add payment_behavior to review_ratings category check constraint

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-04-28
"""
from alembic import op

revision = 'i9j0k1l2m3n4'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None

_OLD = "('communication','professionalism','reliability','quality','brief_adherence','timeline_adherence')"
_NEW = "('communication','professionalism','reliability','quality','brief_adherence','timeline_adherence','payment_behavior')"


def upgrade() -> None:
    op.drop_constraint('ck_review_ratings_category', 'review_ratings', type_='check')
    op.create_check_constraint('ck_review_ratings_category', 'review_ratings', f"category IN {_NEW}")


def downgrade() -> None:
    op.drop_constraint('ck_review_ratings_category', 'review_ratings', type_='check')
    op.create_check_constraint('ck_review_ratings_category', 'review_ratings', f"category IN {_OLD}")
