"""Add nullable indexed TOPIQ-NR quality observations."""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e0f1a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('quality_score', sa.Float(), nullable=True))
    op.create_index('ix_media_quality_score', 'media', ['quality_score'])


def downgrade():
    op.drop_index('ix_media_quality_score', table_name='media')
    op.drop_column('media', 'quality_score')
