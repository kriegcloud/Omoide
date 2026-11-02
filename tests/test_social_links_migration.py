import importlib.util
from pathlib import Path
import unittest

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


class SocialMigrationTests(unittest.TestCase):
    def test_offline_backfill_and_duplicate_urls(self):
        path = Path(__file__).parents[1] / 'alembic/versions/c9d0e1f20314_social_link_brands.py'
        spec = importlib.util.spec_from_file_location('social_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = sa.create_engine('sqlite://')
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE person (id INTEGER PRIMARY KEY)')
            conn.exec_driver_sql('CREATE TABLE personsociallink (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id), platform VARCHAR NOT NULL, handle VARCHAR NOT NULL, url VARCHAR NOT NULL, created_at DATETIME NOT NULL, CONSTRAINT uq_person_social_link UNIQUE(person_id,platform,handle))')
            conn.exec_driver_sql("INSERT INTO person VALUES (1)")
            conn.exec_driver_sql("INSERT INTO personsociallink VALUES (1,1,'x','person','http://www.twitter.com/person/?utm_source=x','2026-01-01'),(2,1,'other','site','example.com/me','2026-01-02'),(3,1,'other','alias','https://x.com/person','2026-01-03'),(4,1,'other','broken','not a URL','2026-01-04')")
            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
            rows = conn.execute(sa.text('SELECT platform,normalized_url,brand_domain,sort_order FROM personsociallink ORDER BY id')).all()
            self.assertEqual(rows, [('x','https://x.com/person','x.com',0),('other','https://example.com/me','example.com',1),('x',None,'x.com',2),('other',None,None,3)])
            self.assertEqual(conn.execute(sa.text("SELECT status FROM social_brand WHERE domain='x.com'")).scalar(), 'ready')
            module.downgrade()
            self.assertEqual(conn.execute(sa.text('SELECT count(*) FROM personsociallink')).scalar(), 4)
        engine.dispose()
