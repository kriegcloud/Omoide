"""Allow manual exclusion of unassigned faces from review and inference."""
from alembic import op
import sqlalchemy as sa

revision = 'f0611a9c2e74'
down_revision = '598e24827831'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('face', sa.Column('ignored_at', sa.DateTime(), nullable=True))
    op.create_index('ix_face_ignored_at', 'face', ['ignored_at'])
    # Persistent counter invalidates caches across processes and unignore cycles.
    op.execute('CREATE TABLE face_review_state (id INTEGER PRIMARY KEY, ignored_generation INTEGER NOT NULL DEFAULT 0)')
    op.execute('INSERT INTO face_review_state VALUES (1, 0)')
    for event in ('INSERT', 'DELETE', 'UPDATE'):
        label = event.split()[0].lower()
        op.execute(f'CREATE TRIGGER face_review_{label} AFTER {event} ON face BEGIN UPDATE face_review_state SET ignored_generation = ignored_generation + 1 WHERE id = 1; END')


def downgrade():
    for label in ('insert', 'delete', 'update'):
        op.execute(f'DROP TRIGGER face_review_{label}')
    op.drop_table('face_review_state')
    op.drop_index('ix_face_ignored_at', table_name='face')
    op.drop_column('face', 'ignored_at')
