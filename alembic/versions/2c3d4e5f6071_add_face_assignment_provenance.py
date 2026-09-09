"""Persist the latest face assignment's time and source.

Revision ID: 2c3d4e5f6071
Revises: 1b2c3d4e5f60
"""

import sqlalchemy as sa
from alembic import op

revision = "2c3d4e5f6071"
down_revision = "1b2c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("face") as batch_op:
        batch_op.add_column(sa.Column("assigned_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("assignment_source", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("face") as batch_op:
        batch_op.drop_column("assignment_source")
        batch_op.drop_column("assigned_at")
