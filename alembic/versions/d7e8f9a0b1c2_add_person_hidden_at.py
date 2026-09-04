"""Add hidden_at to person

Revision ID: d7e8f9a0b1c2
Revises: b4c5d6e7f8a9
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("person", sa.Column("hidden_at", sa.DateTime(), nullable=True))
    op.create_index(op.f("ix_person_hidden_at"), "person", ["hidden_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_person_hidden_at"), table_name="person")
    op.drop_column("person", "hidden_at")
