"""add dataset item reviewed marker

Revision ID: d7e8f9a0b1c3
Revises: d7e8f9a0b1c4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c3"
down_revision: str | None = "d7e8f9a0b1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasetitem",
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_datasetitem_reviewed_at",
        "datasetitem",
        ["reviewed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_datasetitem_reviewed_at", table_name="datasetitem")
    op.drop_column("datasetitem", "reviewed_at")
