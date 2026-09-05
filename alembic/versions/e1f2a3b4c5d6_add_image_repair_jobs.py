"""add image repair jobs

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imagerepairjob",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created", "queued", "running", "succeeded", "failed", "cancelled",
                name="imagerepairstatus",
            ),
            nullable=False,
        ),
        sa.Column("external_prompt_id", sa.String(), nullable=True),
        sa.Column("result_media_id", sa.Integer(), nullable=True),
        sa.Column("mask_path", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_media_id"], ["media.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_prompt_id"),
    )
    for column in (
        "media_id", "profile", "status", "external_prompt_id", "result_media_id",
        "error_code", "created_at",
    ):
        op.create_index(f"ix_imagerepairjob_{column}", "imagerepairjob", [column])


def downgrade() -> None:
    op.drop_table("imagerepairjob")
