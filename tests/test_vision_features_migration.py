"""Additive lane migration defaults and recovery schema without a live database."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations


class VisionFeaturesMigrationTests(unittest.TestCase):
    def test_upgrade_existing_row_constraints_and_downgrade(self):
        path=Path(__file__).parents[1]/'alembic/versions/b8c9d0e1f203_person_detection_watermarks.py'
        spec=importlib.util.spec_from_file_location('lane_v_migration',path)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision,'f0611a9c2e74')
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE media (id INTEGER PRIMARY KEY)')
            conn.exec_driver_sql('INSERT INTO media VALUES (1)')
            with patch.object(migration,'op',Operations(MigrationContext.configure(conn))):
                migration.upgrade()
                self.assertEqual(conn.exec_driver_sql('SELECT persons_detected, has_persons FROM media').one(),(0,None))
                self.assertEqual(conn.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
                indexes={i['name'] for i in inspect(conn).get_indexes('media')}
                self.assertTrue({'ix_media_persons_detected','ix_media_has_persons'}<=indexes)
                self.assertIn('watermark_candidate',inspect(conn).get_table_names())
                migration.downgrade()
                self.assertNotIn('watermark_candidate',inspect(conn).get_table_names())
                self.assertEqual([c['name'] for c in inspect(conn).get_columns('media')],['id'])
