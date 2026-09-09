"""Add person pair decisions for the merge queue.

Revision ID: 0a1b2c3d4e5f
Revises: f9a0b1c2d3e5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "f9a0b1c2d3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person_pair_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_a_id", sa.Integer(), nullable=False),
        sa.Column("person_b_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_a_id"], ["person.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_b_id"], ["person.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_a_id", "person_b_id", name="uq_person_pair_decision"),
        sa.CheckConstraint("person_a_id < person_b_id", name="ck_person_pair_decision_order"),
        sa.CheckConstraint("decision = 'not_same'", name="ck_person_pair_decision_value"),
    )
    op.create_index(
        "ix_person_pair_decision_person_b_id", "person_pair_decision", ["person_b_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_person_pair_decision_person_b_id", table_name="person_pair_decision")
    op.drop_table("person_pair_decision")
