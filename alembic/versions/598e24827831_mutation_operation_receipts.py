"""Durable synchronous mutation intents and receipts."""
from alembic import op
import sqlalchemy as sa

revision = '598e24827831'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mutationoperation',
        sa.Column('id', sa.String(), primary_key=True, nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('receipt', sa.JSON(), nullable=False),
        sa.Column('response', sa.JSON(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('mutationoperation')
