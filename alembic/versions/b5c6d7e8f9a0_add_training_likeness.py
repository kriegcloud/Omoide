"""add training likeness evaluation

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trainingsample", sa.Column("likeness", sa.Float(), nullable=True))
    op.add_column("trainingsample", sa.Column("face_count", sa.Integer(), nullable=True))
    op.add_column("trainingsample", sa.Column("face_bbox", sa.JSON(), nullable=True))
    op.add_column("trainingsample", sa.Column("scored_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_trainingsample_scored_at", "trainingsample", ["scored_at"]
    )
    op.add_column(
        "trainingrun", sa.Column("likeness_best_step", sa.Integer(), nullable=True)
    )
    op.add_column("trainingrun", sa.Column("likeness_best", sa.Float(), nullable=True))
    op.add_column("trainingrun", sa.Column("likeness_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("trainingrun", "likeness_summary")
    op.drop_column("trainingrun", "likeness_best")
    op.drop_column("trainingrun", "likeness_best_step")
    op.drop_index("ix_trainingsample_scored_at", table_name="trainingsample")
    op.drop_column("trainingsample", "scored_at")
    op.drop_column("trainingsample", "face_bbox")
    op.drop_column("trainingsample", "face_count")
    op.drop_column("trainingsample", "likeness")
