import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models import Media


class MigrationAuditTests(unittest.TestCase):
    def test_full_upgrade_downgrade_upgrade_on_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'cycle.db'}"
            with patch.object(type(settings.general), "database_url", new_callable=PropertyMock, return_value=url):
                config = Config("alembic.ini")
                command.upgrade(config, "head")
                command.downgrade(config, "base")
                command.upgrade(config, "head")
            engine = create_engine(url)
            self.addCleanup(engine.dispose)
            self.assertIn("media", inspect(engine).get_table_names())

    def test_existing_table_migrations_propagate_other_operational_errors(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        for revision in ("94d1f4d9ef32", "70b51c6758b2"):
            module = script.get_revision(revision).module
            with self.subTest(revision=revision):
                failure = OperationalError(
                    "CREATE TABLE", {}, sqlite3.OperationalError("database is locked")
                )
                with patch.object(module.op, "create_table", side_effect=failure):
                    with self.assertRaises(OperationalError):
                        module.upgrade()

    def test_existing_table_migrations_still_accept_already_exists(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        for revision in ("94d1f4d9ef32", "70b51c6758b2"):
            module = script.get_revision(revision).module
            with self.subTest(revision=revision):
                failure = OperationalError(
                    "CREATE TABLE", {}, sqlite3.OperationalError("table already exists")
                )
                with (
                    patch.object(module.op, "create_table", side_effect=failure),
                    patch.object(module.op, "create_index"),
                ):
                    module.upgrade()


class MediaDerivedColumnTests(unittest.TestCase):
    def test_orm_insert_and_changes_keep_folder_and_pixel_count_current(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine, tables=[Media.__table__])
        with Session(engine) as session:
            media = Media(path="/photos/100%_done/a.jpg", filename="a.jpg", size=1,
                          width=20, height=30)
            session.add(media)
            session.commit()
            session.refresh(media)
            self.assertEqual(media.folder, "/photos/100%_done")
            self.assertEqual(media.pixel_count, 600)

            media.path = "C:\\photos\\renamed\\a.jpg"
            media.width = 10
            session.commit()
            session.refresh(media)
            self.assertEqual(media.folder, "C:/photos/renamed")
            self.assertEqual(media.pixel_count, 300)

            media.height = None
            session.commit()
            session.refresh(media)
            self.assertIsNone(media.pixel_count)

    def test_migration_backfills_normalized_parent_and_nullable_dimensions(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        revision = script.get_revision("3d4e5f607182")
        self.assertEqual(revision.down_revision, "2c3d4e5f6071")
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'backfill.db'}"
            with patch.object(type(settings.general), "database_url", new_callable=PropertyMock, return_value=url):
                command.upgrade(Config("alembic.ini"), "2c3d4e5f6071")
            engine = create_engine(url)
            self.addCleanup(engine.dispose)
            with engine.begin() as connection:
                for index, (path, width, height) in enumerate((
                    ("/photos/100%_done/a.jpg", 20, 30),
                    ("C:\\photos\\a.jpg", 40, 50),
                    ("/a.jpg", None, 50),
                    ("a.jpg", 0, 50),
                ), 1):
                    connection.exec_driver_sql(
                        "INSERT INTO media (id, path, filename, size, width, height, "
                        "views, inserted_at, created_at, faces_extracted, embeddings_created, "
                        "ran_auto_tagging, extracted_scenes, is_favorite, missing_confirmed) "
                        "VALUES (?, ?, 'a.jpg', 1, ?, ?, 0, CURRENT_TIMESTAMP, "
                        "CURRENT_TIMESTAMP, 0, 0, 0, 0, 0, 0)",
                        (index, path, width, height),
                    )
                with Operations.context(MigrationContext.configure(connection)):
                    revision.module.upgrade()
                self.assertEqual(connection.exec_driver_sql(
                    "SELECT folder, pixel_count FROM media ORDER BY id"
                ).all(), [("/photos/100%_done", 600), ("C:/photos", 2000), ("/", None), ("", 0)])


if __name__ == "__main__":
    unittest.main()
