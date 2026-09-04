import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.models import Face, Media, Person, PersonMediaLink  # noqa: E402
from app.schemas.person import (  # noqa: E402
    PersonMediaBulkRequest,
    PersonMediaReassignRequest,
)

person_api = importlib.import_module("app.api.person")


class PersonMediaBulkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                Person.__table__,
                Face.__table__,
                PersonMediaLink.__table__,
            ],
        )
        self.session = Session(self.engine)
        self.source_person = Person(name="Source", appearance_count=0)
        self.target_person = Person(name="Target", appearance_count=0)
        self.media = [
            Media(path=f"/media/{index}.jpg", filename=f"{index}.jpg", size=10)
            for index in range(1, 5)
        ]
        self.session.add_all([self.source_person, self.target_person, *self.media])
        self.session.commit()
        for item in [self.source_person, self.target_person, *self.media]:
            self.session.refresh(item)
        self.helper_patches = [
            patch.object(person_api, "recalculate_person_appearance_counts"),
            patch.object(person_api, "update_person_embedding"),
            patch("app.api.face.update_face_embedding"),
            patch("app.api.face.old_person_can_be_deleted", return_value=False),
        ]
        for helper_patch in self.helper_patches:
            helper_patch.start()

    def tearDown(self) -> None:
        for helper_patch in reversed(self.helper_patches):
            helper_patch.stop()
        self.session.close()
        self.engine.dispose()

    def add_face(self, person_id: int, media_id: int) -> Face:
        face = Face(person_id=person_id, media_id=media_id, bbox=[0, 0, 1, 1])
        self.session.add(face)
        self.session.commit()
        self.session.refresh(face)
        return face

    def test_bulk_attach_skips_media_with_detected_face(self) -> None:
        self.add_face(self.source_person.id, self.media[0].id)
        result = person_api.attach_media_to_person_bulk(
            self.source_person.id,
            PersonMediaBulkRequest(
                media_ids=[self.media[0].id, self.media[1].id, 9999]
            ),
            self.session,
        )

        self.assertEqual(result.added_ids, [self.media[1].id])
        self.assertEqual(result.skipped_ids, [self.media[0].id, 9999])
        link = self.session.exec(
            select(PersonMediaLink).where(
                PersonMediaLink.person_id == self.source_person.id,
                PersonMediaLink.media_id == self.media[1].id,
            )
        ).first()
        self.assertIsNotNone(link)

    def test_bulk_detach_removes_faces_and_manual_links(self) -> None:
        face = self.add_face(self.source_person.id, self.media[0].id)
        self.session.add(
            PersonMediaLink(
                person_id=self.source_person.id,
                media_id=self.media[1].id,
            )
        )
        self.session.commit()

        result = person_api.detach_media_from_person_bulk(
            self.source_person.id,
            PersonMediaBulkRequest(
                media_ids=[self.media[0].id, self.media[1].id, self.media[2].id]
            ),
            self.session,
        )

        self.assertEqual(result.detached_ids, [self.media[0].id, self.media[1].id])
        self.assertEqual(result.skipped_ids, [self.media[2].id])
        self.session.refresh(face)
        self.assertIsNone(face.person_id)
        self.assertIsNone(
            self.session.exec(
                select(PersonMediaLink).where(
                    PersonMediaLink.person_id == self.source_person.id
                )
            ).first()
        )

    def test_reassign_moves_all_faces_for_media(self) -> None:
        first = self.add_face(self.source_person.id, self.media[0].id)
        second = self.add_face(self.source_person.id, self.media[0].id)

        result = person_api.reassign_media_to_person(
            self.source_person.id,
            self.media[0].id,
            PersonMediaReassignRequest(target_person_id=self.target_person.id),
            self.session,
        )

        self.assertTrue(result.reassigned)
        self.session.refresh(first)
        self.session.refresh(second)
        self.assertEqual(first.person_id, self.target_person.id)
        self.assertEqual(second.person_id, self.target_person.id)

    def test_reassign_moves_manual_link_when_there_are_no_faces(self) -> None:
        link = PersonMediaLink(
            person_id=self.source_person.id,
            media_id=self.media[0].id,
        )
        self.session.add(link)
        self.session.commit()

        result = person_api.reassign_media_to_person(
            self.source_person.id,
            self.media[0].id,
            PersonMediaReassignRequest(target_person_id=self.target_person.id),
            self.session,
        )

        self.assertTrue(result.reassigned)
        moved = self.session.exec(
            select(PersonMediaLink).where(
                PersonMediaLink.person_id == self.target_person.id,
                PersonMediaLink.media_id == self.media[0].id,
            )
        ).first()
        self.assertIsNotNone(moved)


if __name__ == "__main__":
    unittest.main()
