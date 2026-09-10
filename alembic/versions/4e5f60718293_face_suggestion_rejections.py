"""Persist rejected face/person suggestions.

Revision ID: 4e5f60718293
Revises: 3d4e5f607182
"""

import sqlalchemy as sa
from alembic import op

revision = "4e5f60718293"
down_revision = "3d4e5f607182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "face_suggestion_rejection",
        sa.Column("face_id", sa.Integer(), sa.ForeignKey("face.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("face_suggestion_rejection")
