"""add training run base model

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trainingrun",
        sa.Column(
            "base_model",
            sa.String(),
            nullable=False,
            server_default="flux-dev",
        ),
    )


def downgrade() -> None:
    op.drop_column("trainingrun", "base_model")
