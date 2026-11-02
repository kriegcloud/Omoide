"""Avatar picker ordering and parent geometry on a synthetic SQLite library."""
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine

from app.api.person.avatar import avatar_faces
from app.models import Face, Media, Person


class AvatarPickerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.person = Person(name="Synthetic", appearance_count=0)
        self.session.add(self.person)
        self.session.commit()
        self.session.refresh(self.person)
        self.faces = []
        for i, (score, frontality) in enumerate(((.9, .2), (.7, .9), (.7, .9))):
            media = Media(path=f"/synthetic/{i}.jpg", filename=f"{i}.jpg", size=1,
                          width=2000, height=1000, thumbnail_path=f"parent-{i}.jpg",
                          created_at=datetime(2026, 1, 1) + timedelta(days=i))
            self.session.add(media)
            self.session.flush()
            face = Face(media_id=media.id, person_id=self.person.id, thumbnail_path=f"face-{i}.jpg",
                        bbox=[1, 2, 30, 40], det_score=score, frontality=frontality)
            self.session.add(face)
            self.session.flush()
            self.faces.append(face)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def page(self, **kwargs):
        return avatar_faces(self.person.id, session=self.session, limit=1, **kwargs)

    def test_quality_product_and_ties_use_id(self):
        first = self.page()
        second = self.page(cursor=first.next_cursor)
        third = self.page(cursor=second.next_cursor)
        self.assertEqual([first.items[0].id, second.items[0].id, third.items[0].id],
                         [self.faces[2].id, self.faces[1].id, self.faces[0].id])
        self.assertIsNone(third.next_cursor)
        self.assertEqual(first.items[0].bbox, [1, 2, 30, 40])
        self.assertEqual(first.items[0].media_thumbnail_path, "parent-2.jpg")

    def test_newest_sort_and_cursor_scope(self):
        first = self.page(sort="newest")
        self.assertEqual(first.items[0].id, self.faces[2].id)
        with self.assertRaises(HTTPException) as raised:
            self.page(sort="quality", cursor=first.next_cursor)
        self.assertEqual(raised.exception.status_code, 400)

    def test_missing_and_ignored_faces_are_excluded(self):
        self.faces[2].ignored_at = datetime.now()
        self.faces[1].media.missing_since = datetime.now()
        self.session.commit()
        result = self.page()
        self.assertEqual(result.items[0].id, self.faces[0].id)
        self.assertIsNone(result.next_cursor)
