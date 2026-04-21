"""add_social_accounts_partial_index

Primary-account lookups (one per platform per user) dominate reads. A partial
index on is_primary = true keeps the index tiny and makes the common "give me
user X's primary youtube account" query an index-only scan.

Revision ID: a1b2c3d4e5f6
Revises: 19af77bf7ee4
Create Date: 2026-04-20 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '19af77bf7ee4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_social_accounts_primary',
        'social_accounts',
        ['user_id', 'platform'],
        unique=False,
        postgresql_where='is_primary = true',
    )


def downgrade() -> None:
    op.drop_index('ix_social_accounts_primary', table_name='social_accounts')
