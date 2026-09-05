"""merge annotation and workstation ux heads

Revision ID: f26ceee70778
Revises: a0b1c2d3e4f5, c5d6e7f8a9b0
Create Date: 2026-09-04 20:05:14.224667

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f26ceee70778'
down_revision: Union[str, None] = ('a0b1c2d3e4f5', 'c5d6e7f8a9b0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
