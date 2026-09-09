"""Persist processing task parameters for explicit resume.

Revision ID: 1b2c3d4e5f60
Revises: 0a1b2c3d4e5f
"""

import sqlalchemy as sa
from alembic import op

revision = "1b2c3d4e5f60"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The initial migration uses an unconstrained string for status. The later
    # 70b51c6758b2_remove_broken_status migration only deletes invalid rows;
    # neither installs a CHECK constraint that needs to accept "interrupted".
    op.add_column("processingtask", sa.Column("params", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Older code cannot deserialize the new enum value.
    op.execute("UPDATE processingtask SET status = 'cancelled' WHERE status = 'interrupted'")
    op.drop_column("processingtask", "params")
