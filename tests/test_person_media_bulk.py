import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import Face, Media, Person, PersonMediaLink  # noqa: E402
from app.schemas.person import (  # noqa: E402
    PersonMediaBulkRequest,
    PersonMediaReassignRequest,
)

person_api = importlib.import_module("app.api.person")
face_api = importlib.import_module("app.api.face")


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
        self.assertEqual(
            [item.model_dump() for item in result.detached_faces],
            [{"id": face.id, "media_id": self.media[0].id}],
        )
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


class PersonMediaUndoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE face_embeddings USING vec0("
                "face_id integer primary key, person_id integer, embedding float[512])"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE person_embeddings USING vec0("
                "person_id integer, embedding float[512])"
            )
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.person = Person(name="Undo person", appearance_count=0)
        self.other = Person(name="Other person", appearance_count=0)
        self.media = [
            Media(path=f"/fake/undo-{index}.jpg", filename=f"{index}.jpg", size=1)
            for index in range(4)
        ]
        self.session.add_all([self.person, self.other, *self.media])
        self.session.commit()
        self.person_id = self.person.id
        app = FastAPI()
        app.include_router(person_api.router, prefix="/api/person")
        app.include_router(face_api.router, prefix="/api/faces")
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def add_face(self, media_id: int, person_id: int | None) -> int:
        face = Face(media_id=media_id, person_id=person_id, bbox=[0, 0, 1, 1])
        self.session.add(face)
        self.session.commit()
        face_id = face.id
        # Exercise the actual embedding update as well as the Face row.
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (?, ?, ?)",
                (face_id, person_id if person_id is not None else -1, bytes(512 * 4)),
            )
        return face_id

    def assert_face_person(self, face_id: int, person_id: int | None) -> None:
        self.session.expire_all()
        self.assertEqual(self.session.get(Face, face_id).person_id, person_id)
        with self.engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT person_id FROM face_embeddings WHERE face_id = ?", (face_id,)
            ).one()
        self.assertEqual(row[0], person_id if person_id is not None else -1)

    def restore(self, detached_faces: list[dict], media_ids: list[int]) -> dict:
        if detached_faces:
            response = self.client.post(
                "/api/faces/assign",
                json={
                    "face_ids": [face["id"] for face in detached_faces],
                    "person_id": self.person_id,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            for face in detached_faces:
                self.assert_face_person(face["id"], self.person_id)
        response = self.client.post(
            f"/api/person/{self.person_id}/media/bulk", json={"media_ids": media_ids}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_bulk_detach_and_undo_restore_exact_faces_and_manual_appearances(self):
        first, manual, second, untouched = [media.id for media in self.media]
        faces = [
            {"id": self.add_face(media_id, self.person_id), "media_id": media_id}
            for media_id in (first, first, second)
        ]
        other_face = self.add_face(first, self.other.id)
        orphan_face = self.add_face(first, None)
        untouched_face = self.add_face(untouched, self.person_id)
        self.session.add_all([
            PersonMediaLink(person_id=self.person_id, media_id=manual),
            PersonMediaLink(person_id=self.person_id, media_id=first),
        ])
        self.session.commit()

        response = self.client.post(
            f"/api/person/{self.person_id}/media/bulk-detach",
            json={"media_ids": [first, manual, second, first, 9999]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["detached_ids"], [first, manual, second])
        self.assertEqual(result["skipped_ids"], [9999])
        self.assertCountEqual(result["detached_faces"], faces)
        for face in faces:
            self.assert_face_person(face["id"], None)
        self.assert_face_person(other_face, self.other.id)
        self.assert_face_person(orphan_face, None)
        self.assert_face_person(untouched_face, self.person_id)
        self.assertEqual(self.session.exec(select(PersonMediaLink)).all(), [])
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 1)

        inverse = self.restore(result["detached_faces"], result["detached_ids"])
        self.assertEqual(inverse, {"added_ids": [manual], "skipped_ids": [first, second]})
        self.session.expire_all()
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 4)
        self.assertEqual(
            self.session.exec(select(PersonMediaLink.media_id)).all(), [manual]
        )

    def test_single_detach_returns_all_faces_and_last_appearance_can_be_restored(self):
        media_id = self.media[0].id
        faces = [
            {"id": self.add_face(media_id, self.person_id), "media_id": media_id}
            for _ in range(2)
        ]
        other_face = self.add_face(media_id, self.other.id)
        response = self.client.post(
            f"/api/person/{self.person_id}/media/{media_id}/detach"
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["message"], "Media detached from person")
        self.assertCountEqual(result["detached_faces"], faces)
        for face in faces:
            self.assert_face_person(face["id"], None)
        self.assert_face_person(other_face, self.other.id)
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 0)
        inverse = self.restore(result["detached_faces"], [media_id])
        self.assertEqual(inverse, {"added_ids": [], "skipped_ids": [media_id]})
        self.session.expire_all()
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 1)
        self.assertEqual(self.session.exec(select(PersonMediaLink)).all(), [])

    def test_single_manual_detach_returns_empty_faces_and_restores_link(self):
        media_id = self.media[0].id
        self.session.add(PersonMediaLink(person_id=self.person_id, media_id=media_id))
        self.session.commit()
        response = self.client.post(
            f"/api/person/{self.person_id}/media/{media_id}/detach"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "message": "Media detached from person", "detached_faces": [],
        })
        self.assertEqual(self.session.exec(select(PersonMediaLink)).all(), [])
        self.assertEqual(self.restore([], [media_id]), {
            "added_ids": [media_id], "skipped_ids": [],
        })
        self.assertEqual(self.session.exec(select(PersonMediaLink.media_id)).all(), [media_id])

    def test_bulk_noop_returns_empty_faces_and_never_restores_skipped_media(self):
        media_id = self.media[0].id
        other_face = self.add_face(media_id, self.other.id)
        for requested, skipped in (([], []), ([media_id, 9999, media_id], [media_id, 9999])):
            with self.subTest(requested=requested):
                response = self.client.post(
                    f"/api/person/{self.person_id}/media/bulk-detach",
                    json={"media_ids": requested},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {
                    "detached_ids": [], "skipped_ids": skipped, "detached_faces": [],
                })
        self.assert_face_person(other_face, self.other.id)


if __name__ == "__main__":
    unittest.main()
