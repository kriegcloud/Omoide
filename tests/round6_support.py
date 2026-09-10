"""Small real SQLite fixture shared by the round-six regression modules."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_CONFIG = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG.name)

import app.api  # noqa: E402,F401
from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners  # noqa: E402
from app.models import Face, Media, Person  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import func  # noqa: E402


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite://")
        _attach_engine_listeners(self.engine)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            for table, key, extras in (
                ("face_embeddings", "face_id", "person_id integer,"),
                ("person_embeddings", "person_id", ""),
                ("media_embeddings", "media_id", ""),
                ("scene_embeddings", "scene_id", "media_id integer,"),
            ):
                conn.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE {table} USING vec0("
                    f"{key} integer primary key, {extras} embedding float[512])"
                )
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.enterContext(patch.object(type(settings.general), "thumb_dir", property(lambda _: self.root)))

    def media(self, **kwargs):
        count = self.session.exec(select(func.count(Media.id))).one()
        kwargs.setdefault("path", str(self.root / f"media-{count}.jpg"))
        kwargs.setdefault("filename", Path(kwargs["path"]).name)
        kwargs.setdefault("size", 1)
        row = Media(**kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def person(self, **kwargs):
        row = Person(name="Person", appearance_count=0, **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def face(self, media=None, person=None, **kwargs):
        row = Face(media_id=(media or self.media()).id,
                   person_id=person.id if person else None, bbox=[0, 0, 20, 20], **kwargs)
        self.session.add(row)
        self.session.commit()
        return row
