"""add payment flag types to review_flags check constraint

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-04-28
"""
from alembic import op

revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None

_OLD_TYPES = "('ghosted','missed_deadline','scope_creep','rude_behavior','contract_violation')"
_NEW_TYPES = "('ghosted','missed_deadline','scope_creep','rude_behavior','contract_violation','payment_not_made','payment_partial','payment_refused','payment_delayed','invoice_disputed')"


def upgrade() -> None:
    op.drop_constraint('ck_review_flags_type', 'review_flags', type_='check')
    op.create_check_constraint(
        'ck_review_flags_type',
        'review_flags',
        f"type IN {_NEW_TYPES}",
    )


def downgrade() -> None:
    op.drop_constraint('ck_review_flags_type', 'review_flags', type_='check')
    op.create_check_constraint(
        'ck_review_flags_type',
        'review_flags',
        f"type IN {_OLD_TYPES}",
    )
