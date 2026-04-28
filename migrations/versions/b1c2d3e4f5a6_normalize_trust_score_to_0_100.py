"""normalize trust_score from 300-900 scale to 30-90 scale (divide by 10)

Revision ID: b1c2d3e4f5a6
Revises: a86d6228bbc8
Create Date: 2026-04-28
"""
from alembic import op

revision: str = 'b1c2d3e4f5a6'
down_revision: str = 'a86d6228bbc8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE profiles SET trust_score = ROUND(trust_score / 10.0)")


def downgrade() -> None:
    op.execute("UPDATE profiles SET trust_score = trust_score * 10")
