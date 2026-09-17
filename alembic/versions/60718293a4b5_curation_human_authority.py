"""Add grant-bound passkeys and single-use curation review challenges."""
from alembic import op
import sqlalchemy as sa

revision = '60718293a4b5'
down_revision = '5f60718293a4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('curation_review', sa.Column('presence_evidence', sa.JSON(), nullable=False, server_default='{}'))
    op.create_table('curation_credential',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('grant_id', sa.String(), sa.ForeignKey('curation_grant.id'), nullable=False, unique=True),
        sa.Column('credential_id', sa.String(), nullable=False, unique=True),
        sa.Column('public_key', sa.String(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('rp_id', sa.String(), nullable=False),
        sa.Column('origin', sa.String(), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False),
        sa.Column('registration_sha256', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('curation_challenge',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('grant_id', sa.String(), sa.ForeignKey('curation_grant.id'), nullable=False),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('challenge', sa.String(), nullable=False, unique=True),
        sa.Column('request_sha256', sa.String(), nullable=False),
        sa.Column('credential_id', sa.String(), sa.ForeignKey('curation_credential.id'), nullable=True),
        sa.Column('rp_id', sa.String(), nullable=False),
        sa.Column('origin', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_curation_challenge_grant_id', 'curation_challenge', ['grant_id'])


def downgrade():
    bind = op.get_bind()
    for table in ('curation_credential', 'curation_challenge'):
        if bind.execute(sa.text('SELECT COUNT(*) FROM ' + table)).scalar():
            raise RuntimeError('Refusing downgrade with curation authority evidence; disable the feature instead')
    if bind.execute(sa.text("SELECT COUNT(*) FROM curation_review WHERE presence_evidence != '{}' AND presence_evidence != 'null'")).scalar():
        raise RuntimeError('Refusing downgrade with human review evidence')
    op.drop_index('ix_curation_challenge_grant_id', table_name='curation_challenge')
    op.drop_table('curation_challenge')
    op.drop_table('curation_credential')
    op.drop_column('curation_review', 'presence_evidence')
