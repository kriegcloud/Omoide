"""add training datasets

Revision ID: b8c9d0e1f2a3
Revises: f26ceee70778
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "f26ceee70778"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trainingdataset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("trigger_word", sa.String(), nullable=False),
        sa.Column("class_token", sa.String(), nullable=False),
        sa.Column("caption_source", sa.String(), nullable=False),
        sa.Column("caption_template", sa.String(), nullable=False),
        sa.Column("target_resolution", sa.Integer(), nullable=False),
        sa.Column("buckets", sa.JSON(), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        sa.Column("export_layout", sa.String(), nullable=False),
        sa.Column("cover_media_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["cover_media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    for column in ("name", "slug", "person_id", "created_at", "updated_at"):
        op.create_index(f"ix_trainingdataset_{column}", "trainingdataset", [column])

    op.create_table(
        "datasetitem",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("edit_ops", sa.JSON(), nullable=True),
        sa.Column("edit_design_state", sa.JSON(), nullable=True),
        sa.Column("caption_override", sa.String(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["trainingdataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "media_id", name="uq_datasetitem_dataset_media"),
    )
    for column in ("dataset_id", "media_id", "position", "excluded", "created_at"):
        op.create_index(f"ix_datasetitem_{column}", "datasetitem", [column])

    op.create_table(
        "datasetexport",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("layout", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("output_dir", sa.String(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["trainingdataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["processingtask.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("dataset_id", "status", "task_id", "created_at"):
        op.create_index(f"ix_datasetexport_{column}", "datasetexport", [column])


def downgrade() -> None:
    op.drop_table("datasetexport")
    op.drop_table("datasetitem")
    op.drop_table("trainingdataset")
