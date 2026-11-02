"""URL-first social links and offline brand metadata cache."""
from alembic import op
import sqlalchemy as sa

from app.services.social_registry import normalize_social_url, brand_metadata

revision = 'c9d0e1f20314'
down_revision = 'b8c9d0e1f203'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('social_brand',
        sa.Column('domain', sa.String(), primary_key=True),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('simple_icons_slug', sa.String()),
        sa.Column('colour', sa.String()),
        sa.Column('icon_path', sa.String()),
        sa.Column('source', sa.String()),
        sa.Column('fetched_at', sa.DateTime()),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('error', sa.String()))
    for column in (
        sa.Column('normalized_url', sa.String()),
        sa.Column('brand_domain', sa.String()),
        sa.Column('label', sa.String()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
    ):
        op.add_column('personsociallink', column)
    with op.batch_alter_table('personsociallink') as batch:
        batch.drop_constraint('uq_person_social_link', type_='unique')
    conn = op.get_bind()
    rows = conn.execute(sa.text('SELECT id,person_id,url FROM personsociallink ORDER BY person_id,created_at,id')).mappings().all()
    domains = set()
    orders = {}
    # Preserve even malformed legacy rows. They remain editable and do not
    # prevent startup; only valid URLs receive derived metadata.
    for row in rows:
        order = orders.get(row['person_id'], 0)
        orders[row['person_id']] = order + 1
        try:
            values = normalize_social_url(row['url'])
        except ValueError:
            conn.execute(sa.text('UPDATE personsociallink SET sort_order=:sort_order WHERE id=:id'), dict(id=row['id'], sort_order=order))
            continue
        # Legacy rows can have distinct handles pointing at one URL. Preserve
        # every row; give only the earliest a unique dedupe key until edited.
        duplicate = conn.execute(sa.text('SELECT id FROM personsociallink WHERE person_id=:person_id AND normalized_url=:url'),
                                 dict(person_id=row['person_id'], url=values['normalized_url'])).first()
        if duplicate:
            values['normalized_url'] = None
        conn.execute(sa.text('UPDATE personsociallink SET platform=:platform,handle=:handle,url=:url,normalized_url=:normalized_url,brand_domain=:brand_domain,sort_order=:sort_order WHERE id=:id'),
                     dict(values, id=row['id'], sort_order=order))
        domain = values['brand_domain']
        if domain not in domains:
            metadata = brand_metadata(domain, values['platform'])
            known = bool(metadata['simple_icons_slug'])
            conn.execute(sa.text('INSERT INTO social_brand(domain,display_name,simple_icons_slug,colour,source,status) VALUES (:domain,:display_name,:simple_icons_slug,:colour,:source,:status)'),
                         dict(domain=domain, **metadata, source='simple-icons' if known else None, status='ready' if known else 'pending'))
            domains.add(domain)
    with op.batch_alter_table('personsociallink') as batch:
        batch.create_unique_constraint('uq_person_social_link_url', ['person_id', 'normalized_url'])


def downgrade():
    # New URL-first rows may share a platform/handle. The original uniqueness
    # cannot be restored without deleting user links, so retain all rows.
    with op.batch_alter_table('personsociallink') as batch:
        batch.drop_constraint('uq_person_social_link_url', type_='unique')
        for column in ('sort_order', 'label', 'brand_domain', 'normalized_url'):
            batch.drop_column(column)
    op.drop_table('social_brand')
