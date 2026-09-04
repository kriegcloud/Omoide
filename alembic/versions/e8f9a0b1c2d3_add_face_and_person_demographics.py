"""Add face and person demographics

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("face", sa.Column("sex", sa.String(), nullable=True))
    op.add_column("face", sa.Column("sex_score", sa.Float(), nullable=True))
    op.add_column("face", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column(
        "person", sa.Column("gender_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "person",
        sa.Column(
            "gender_manual",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("person", "gender_manual")
    op.drop_column("person", "gender_confidence")
    op.drop_column("face", "age")
    op.drop_column("face", "sex_score")
    op.drop_column("face", "sex")
