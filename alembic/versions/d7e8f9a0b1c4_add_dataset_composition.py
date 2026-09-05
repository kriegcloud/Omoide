"""add dataset composition fields

Revision ID: d7e8f9a0b1c4
Revises: c6d7e8f9a0b1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c4"
down_revision: str | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("face", sa.Column("kps", sa.JSON(), nullable=True))
    op.add_column("face", sa.Column("yaw", sa.Float(), nullable=True))
    op.add_column("face", sa.Column("pitch", sa.Float(), nullable=True))
    op.add_column(
        "trainingdataset", sa.Column("composition_targets", sa.JSON(), nullable=True)
    )
    op.add_column(
        "datasetitem",
        sa.Column("origin", sa.String(), nullable=False, server_default="media"),
    )


def downgrade() -> None:
    op.drop_column("datasetitem", "origin")
    op.drop_column("trainingdataset", "composition_targets")
    op.drop_column("face", "pitch")
    op.drop_column("face", "yaw")
    op.drop_column("face", "kps")
