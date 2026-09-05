"""add training runs

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trainingrun",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("export_id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "requested", "running", "completed", "failed", "cancelled",
                name="trainingrunstatus",
            ),
            nullable=False,
        ),
        sa.Column("run_dir", sa.String(), nullable=False),
        sa.Column("config_yaml", sa.String(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("last_loss", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_sample_step", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["trainingdataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["export_id"], ["datasetexport.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("dataset_id", "export_id", "status", "created_at"):
        op.create_index(f"ix_trainingrun_{column}", "trainingrun", [column])

    op.create_table(
        "trainingsample",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["trainingrun.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "path", name="uq_trainingsample_run_path"),
    )
    for column in ("run_id", "step", "created_at"):
        op.create_index(f"ix_trainingsample_{column}", "trainingsample", [column])


def downgrade() -> None:
    op.drop_table("trainingsample")
    op.drop_table("trainingrun")
