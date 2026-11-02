"""Record raw AdaFace embedding magnitude without changing matching gates."""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('face', sa.Column('embedding_norm', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('face', 'embedding_norm')
