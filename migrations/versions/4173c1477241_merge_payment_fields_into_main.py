"""merge_payment_fields_into_main

Revision ID: 4173c1477241
Revises: d3e4f5a6b7c8, j0k1l2m3n4o5
Create Date: 2026-04-29 13:57:51.752873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4173c1477241'
down_revision: Union[str, None] = ('d3e4f5a6b7c8', 'j0k1l2m3n4o5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
