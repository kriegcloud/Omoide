"""add dataset item exclusion reason

Revision ID: e8f9a0b1c2d4
Revises: d7e8f9a0b1c3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d4"
down_revision: str | None = "d7e8f9a0b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasetitem",
        sa.Column("excluded_reason", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datasetitem", "excluded_reason")
