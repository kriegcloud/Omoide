"""add post-training evaluation kit

Revision ID: f9a0b1c2d3e5
Revises: e8f9a0b1c2d4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e5"
down_revision: str | None = "e8f9a0b1c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evalbatch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint_path", sa.String(), nullable=False),
        sa.Column("lora_path", sa.String(), nullable=False),
        sa.Column("prompts", sa.JSON(), nullable=False),
        sa.Column("seeds", sa.JSON(), nullable=False),
        sa.Column("lora_strength", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["trainingrun.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evalbatch_run_id", "evalbatch", ["run_id"])
    op.create_index("ix_evalbatch_status", "evalbatch", ["status"])
    op.create_index("ix_evalbatch_created_at", "evalbatch", ["created_at"])
    op.create_table(
        "evalsample",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("prompt_index", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("attempt_id", sa.String(), nullable=False),
        sa.Column("likeness", sa.Float(), nullable=True),
        sa.Column("face_count", sa.Integer(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["evalbatch.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index("ix_evalsample_batch_id", "evalsample", ["batch_id"])
    op.create_index("ix_evalsample_attempt_id", "evalsample", ["attempt_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_evalsample_attempt_id", table_name="evalsample")
    op.drop_index("ix_evalsample_batch_id", table_name="evalsample")
    op.drop_table("evalsample")
    op.drop_index("ix_evalbatch_created_at", table_name="evalbatch")
    op.drop_index("ix_evalbatch_status", table_name="evalbatch")
    op.drop_index("ix_evalbatch_run_id", table_name="evalbatch")
    op.drop_table("evalbatch")
