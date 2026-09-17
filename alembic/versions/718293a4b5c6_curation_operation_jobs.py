"""Link admitted curation operations to shared tasks and fence their workers.

Additive only: five nullable/defaulted columns on `curation_operation`. No
existing column is altered and no row is backfilled. The downgrade refuses a
database that already carries job state so a rollback cannot silently discard
the lease/attempt history an in-flight export depends on.
"""
from alembic import op
import sqlalchemy as sa

revision = '718293a4b5c6'
down_revision = '60718293a4b5'
branch_labels = None
depends_on = None


def upgrade():
    # The shared ProcessingTask row that executes this operation. Deliberately
    # not a foreign key: task rows are pruned by the generic task lifecycle and
    # must never be able to delete or block curation provenance.
    op.add_column('curation_operation', sa.Column('task_id', sa.String(), nullable=True))
    op.add_column('curation_operation', sa.Column('lease_worker', sa.String(), nullable=True))
    op.add_column('curation_operation', sa.Column('lease_attempt', sa.String(), nullable=True))
    op.add_column('curation_operation', sa.Column('lease_expires_at', sa.DateTime(), nullable=True))
    op.add_column('curation_operation', sa.Column('progress_done', sa.Integer(), nullable=False,
                                                  server_default='0'))


def downgrade():
    bind = op.get_bind()
    populated = bind.execute(sa.text(
        'SELECT COUNT(*) FROM curation_operation '
        'WHERE task_id IS NOT NULL OR lease_worker IS NOT NULL OR lease_expires_at IS NOT NULL'
    )).scalar()
    if populated:
        raise RuntimeError('Refusing downgrade with curation job state; disable the feature instead')
    op.drop_column('curation_operation', 'progress_done')
    op.drop_column('curation_operation', 'lease_expires_at')
    op.drop_column('curation_operation', 'lease_attempt')
    op.drop_column('curation_operation', 'lease_worker')
    op.drop_column('curation_operation', 'task_id')
