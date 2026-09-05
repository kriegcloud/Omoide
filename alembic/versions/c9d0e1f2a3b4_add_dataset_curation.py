"""add dataset curation

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trainingdataset") as batch_op:
        batch_op.add_column(
            sa.Column("regularization_dataset_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_trainingdataset_regularization_dataset_id",
            "trainingdataset",
            ["regularization_dataset_id"],
            ["id"],
        )
    op.create_table(
        "mediacurationstats",
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("brightness_mean", sa.Float(), nullable=False),
        sa.Column("contrast_std", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("media_id"),
    )


def downgrade() -> None:
    op.drop_table("mediacurationstats")
    with op.batch_alter_table("trainingdataset") as batch_op:
        batch_op.drop_constraint(
            "fk_trainingdataset_regularization_dataset_id", type_="foreignkey"
        )
        batch_op.drop_column("regularization_dataset_id")
