"""add caption review marker

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasetitem",
        sa.Column("caption_reviewed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datasetitem", "caption_reviewed_at")
