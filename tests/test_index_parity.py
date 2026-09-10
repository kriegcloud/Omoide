import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

from app.config import settings
from app import models  # Register every table before checking metadata.


class IndexParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        url = f"sqlite:///{Path(cls.directory.name) / 'parity.db'}"
        with patch.object(type(settings.general), "database_url", new_callable=PropertyMock, return_value=url):
            command.upgrade(Config("alembic.ini"), "head")
        cls.engine = create_engine(url)
        cls.addClassCleanup(cls.engine.dispose)

    def test_every_model_index_exists_after_upgrade(self):
        inspector = inspect(self.engine)
        for table in SQLModel.metadata.tables.values():
            actual = {index["name"]: index for index in inspector.get_indexes(table.name)}
            for column in table.columns:
                if column.index:
                    with self.subTest(table=table.name, column=column.name):
                        self.assertTrue(any(
                            index["column_names"] == [column.name]
                            for index in actual.values()
                        ), f"Missing declared index for {table.name}.{column.name}")
            for index in table.indexes:
                with self.subTest(table=table.name, index=index.name):
                    self.assertIn(index.name, actual)
                    self.assertEqual(actual[index.name]["column_names"], list(index.columns.keys()))

    def test_orphan_and_person_faces_queries_use_an_index(self):
        with self.engine.connect() as connection:
            for predicate in ("person_id IS NULL", "person_id = 7"):
                with self.subTest(predicate=predicate):
                    rows = connection.exec_driver_sql(
                        "EXPLAIN QUERY PLAN SELECT id FROM face WHERE "
                        f"{predicate} ORDER BY id DESC LIMIT 48"
                    ).all()
                    plan = " ".join(row[3] for row in rows)
                    self.assertNotIn("SCAN face", plan)
                    self.assertIn("INDEX", plan)


if __name__ == "__main__":
    unittest.main()
