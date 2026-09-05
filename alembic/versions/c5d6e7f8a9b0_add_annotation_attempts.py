"""Add durable annotation attempts and immutable review revisions.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-04 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(Inspector.from_engine(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "annotationattempt" not in tables:
        op.create_table(
            "annotationattempt",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("media_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("profile_id", sa.String(), nullable=False),
            sa.Column("backend", sa.String(), nullable=False, server_default="comfy"),
            sa.Column("status", sa.String(), nullable=False, server_default="created"),
            sa.Column("active_slot", sa.Integer(), nullable=True, server_default="1"),
            sa.Column("external_prompt_id", sa.String(), nullable=False),
            sa.Column("predecessor_attempt_id", sa.String(), nullable=True),
            sa.Column("input_sha256", sa.String(), nullable=True),
            sa.Column("workflow_sha256", sa.String(), nullable=True),
            sa.Column("raw_result", sa.JSON(), nullable=True),
            sa.Column("normalized_result", sa.JSON(), nullable=True),
            sa.Column("provenance", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("history_acknowledged_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["media_id"], ["media.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["predecessor_attempt_id"],
                ["annotationattempt.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_prompt_id"),
            sa.UniqueConstraint(
                "active_slot", name="uq_annotationattempt_active_slot"
            ),
        )
        for column in (
            "media_id",
            "kind",
            "profile_id",
            "status",
            "external_prompt_id",
            "predecessor_attempt_id",
            "input_sha256",
            "workflow_sha256",
            "error_code",
            "created_at",
            "history_acknowledged_at",
        ):
            op.create_index(
                f"ix_annotationattempt_{column}",
                "annotationattempt",
                [column],
                unique=False,
            )

    tables = _table_names()
    if "mediaannotation" not in tables:
        op.create_table(
            "mediaannotation",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("media_id", sa.Integer(), nullable=False),
            sa.Column("attempt_id", sa.String(), nullable=True),
            sa.Column("parent_id", sa.String(), nullable=True),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("author", sa.String(), nullable=False),
            sa.Column(
                "review_status",
                sa.String(),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column(
                "schema_version",
                sa.String(),
                nullable=False,
                server_default="omoide.annotation/v1",
            ),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("provenance", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approved_key", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(
                ["media_id"], ["media.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["attempt_id"], ["annotationattempt.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["parent_id"], ["mediaannotation.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attempt_id"),
            sa.UniqueConstraint(
                "approved_key", name="uq_mediaannotation_approved_key"
            ),
            sa.UniqueConstraint(
                "media_id", "kind", "revision", name="uq_mediaannotation_revision"
            ),
        )
        for column in (
            "media_id",
            "attempt_id",
            "parent_id",
            "kind",
            "author",
            "review_status",
            "created_at",
        ):
            op.create_index(
                f"ix_mediaannotation_{column}",
                "mediaannotation",
                [column],
                unique=False,
            )


def downgrade() -> None:
    tables = _table_names()
    if "mediaannotation" in tables:
        op.drop_table("mediaannotation")
    tables = _table_names()
    if "annotationattempt" in tables:
        op.drop_table("annotationattempt")
