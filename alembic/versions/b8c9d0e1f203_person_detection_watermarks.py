"""Hybrid person observations and reviewable watermark recovery receipts."""
from alembic import op
import sqlalchemy as sa

revision = 'b8c9d0e1f203'
down_revision = 'f0611a9c2e74'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('person_score', sa.Float(), nullable=True))
    op.add_column('media', sa.Column('person_siglip_margin', sa.Float(), nullable=True))
    op.add_column('media', sa.Column('persons_detected', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('media', sa.Column('has_persons', sa.Boolean(), nullable=True))
    op.create_index('ix_media_persons_detected', 'media', ['persons_detected'])
    op.create_index('ix_media_has_persons', 'media', ['has_persons'])
    op.create_table('watermark_candidate',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('media_id', sa.Integer(), sa.ForeignKey('media.id'), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='proposed'),
        sa.Column('regions', sa.JSON(), nullable=False),
        sa.Column('mask_path', sa.String()),
        sa.Column('preview_path', sa.String()),
        sa.Column('detector_version', sa.String(), nullable=False),
        sa.Column('model_version', sa.String(), nullable=False),
        sa.Column('score', sa.Float()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime()),
        sa.Column('applied_at', sa.DateTime()),
        sa.Column('backup_path', sa.String()),
        sa.Column('original_sha256', sa.String()),
        sa.Column('cleaned_sha256', sa.String()),
        sa.Column('error', sa.String()),
        sa.CheckConstraint("status IN ('proposed','approved','rejected','applied','restored','failed')", name='ck_watermark_candidate_status'))
    for name in ('media_id', 'status', 'created_at'):
        op.create_index(f'ix_watermark_candidate_{name}', 'watermark_candidate', [name])
    op.create_table('watermark_handle',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('text_normalized', sa.String(), nullable=False),
        sa.Column('display_text', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('source_candidate_id', sa.Integer(), sa.ForeignKey('watermark_candidate.id', ondelete='SET NULL')),
        sa.Column('match_count', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_watermark_handle_text_normalized', 'watermark_handle', ['text_normalized'], unique=True)


def downgrade():
    op.drop_table('watermark_handle')
    op.drop_table('watermark_candidate')
    op.drop_index('ix_media_has_persons', 'media')
    op.drop_index('ix_media_persons_detected', 'media')
    for name in ('has_persons', 'persons_detected', 'person_siglip_margin', 'person_score'):
        op.drop_column('media', name)
