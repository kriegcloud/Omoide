import importlib
import os
import tempfile
import unittest
from datetime import UTC, datetime

import numpy as np
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select, text


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import Face, Media, Person, PersonMediaLink, PersonPairDecision  # noqa: E402

person_api = importlib.import_module("app.api.person")


def embedding(first=1.0, second=0.0):
    vector = np.zeros(512, dtype=np.float32)
    vector[:2] = [first, second]
    return vector.tobytes()


class MergeQueueTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _attach_engine_listeners(self.engine)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE person_embeddings USING vec0("
                "person_id integer, embedding float[512])"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE face_embeddings USING vec0("
                "face_id integer primary key, person_id integer, embedding float[512])"
            )
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        previous_mode = settings.general.presentation_mode
        settings.general.presentation_mode = False
        self.addCleanup(setattr, settings.general, "presentation_mode", previous_mode)
        self.people = [
            Person(name="Alpha", appearance_count=3),
            Person(name="Beta", appearance_count=2),
            Person(name=None, appearance_count=1),
            Person(name="Hidden", appearance_count=0, hidden_at=datetime.now(UTC).replace(tzinfo=None)),
            Person(name="No embedding", appearance_count=0),
        ]
        self.session.add_all(self.people)
        self.session.commit()
        self.a, self.b, self.c, self.hidden, self.no_embedding = [p.id for p in self.people]
        for pid, vector in (
            (self.a, embedding()),
            (self.b, embedding(0.8, 0.6)),
            (self.c, embedding(0, 1)),
            (self.hidden, embedding()),
        ):
            self.add_embedding(pid, vector)
        self.session.commit()
        app = FastAPI()
        app.include_router(person_api.merge_queue_router, prefix="/api/persons")
        app.include_router(person_api.router, prefix="/api/person")
        app.dependency_overrides[get_session] = lambda: self.session
        # An in-process ASGI client exercises validation and serialization;
        # it does not open sockets or run the application lifespan.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def add_embedding(self, person_id, vector):
        self.session.exec(
            text("INSERT INTO person_embeddings(person_id, embedding) VALUES (:pid, :vec)")
            .bindparams(pid=person_id, vec=vector)
        )

    def candidates(self, min_similarity=-100, limit=50):
        response = self.client.get(
            "/api/persons/merge-candidates",
            params={"min_similarity": min_similarity, "limit": limit},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    def decide(self, a, b):
        response = self.client.post(
            "/api/persons/pair-decisions",
            json={"person_a_id": a, "person_b_id": b, "decision": "not_same"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_candidates_rank_unique_unordered_pairs_and_exclude_missing_embeddings(self):
        self.add_embedding(self.a, embedding())
        self.session.commit()
        items = self.candidates()
        self.assertEqual(
            [(item["person_a"]["id"], item["person_b"]["id"]) for item in items],
            [(self.a, self.b), (self.b, self.c), (self.a, self.c)],
        )
        self.assertEqual([item["similarity"] for item in items], [80.0, 60.0, 0.0])
        self.assertTrue(all(item["shared_media"] == 0 for item in items))
        self.assertIsNone(items[1]["person_b"]["name"])
        self.assertIsNone(items[0]["person_a"]["profile_face"])

    def test_similarity_matches_existing_similarities_endpoint(self):
        response = self.client.get(f"/api/person/{self.a}/similarities")
        self.assertEqual(response.status_code, 200, response.text)
        scores = {item["id"]: item["similarity"] for item in response.json()}
        for item in self.candidates():
            if item["person_a"]["id"] == self.a:
                self.assertEqual(item["similarity"], scores[item["person_b"]["id"]])

    def test_threshold_is_inclusive_and_limit_applies_after_ranking(self):
        self.assertEqual([item["similarity"] for item in self.candidates(60)], [80, 60])
        self.assertEqual([item["similarity"] for item in self.candidates(60.01)], [80])
        self.assertEqual([item["similarity"] for item in self.candidates(limit=1)], [80])
        self.assertEqual(self.candidates(80.01), [])
        response = self.client.get("/api/persons/merge-candidates")
        self.assertEqual([item["similarity"] for item in response.json()["items"]], [80])

    def test_hidden_people_are_excluded_on_either_side_before_limit(self):
        self.people[0].hidden_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.commit()
        items = self.candidates(limit=1)
        self.assertEqual(
            [(item["person_a"]["id"], item["person_b"]["id"]) for item in items],
            [(self.b, self.c)],
        )

    def test_ties_have_stable_pair_order(self):
        self.add_embedding(self.no_embedding, embedding())
        self.session.commit()
        items = self.candidates()
        ties = [(i["person_a"]["id"], i["person_b"]["id"]) for i in items if i["similarity"] == 80]
        self.assertEqual(ties, [(self.a, self.b), (self.b, self.no_embedding)])

    def test_summary_and_shared_media_include_distinct_manual_and_face_appearances(self):
        media = [Media(path=f"/fake/{i}.jpg", filename=f"{i}.jpg", size=1) for i in range(4)]
        self.session.add_all(media)
        self.session.flush()
        faces = [
            Face(person_id=pid, media_id=media[index].id, bbox=[0, 0, 1, 1], thumbnail_path="/fake/face.jpg")
            for pid, index in [(self.a, 0), (self.a, 0), (self.b, 0), (self.a, 1)]
        ]
        self.session.add_all(faces)
        self.session.flush()
        self.people[0].profile_face_id = faces[0].id
        self.session.add_all([
            PersonMediaLink(person_id=pid, media_id=media[index].id)
            for pid, index in [(self.a, 0), (self.b, 0), (self.b, 1), (self.a, 2), (self.b, 2), (self.c, 3)]
        ])
        self.session.commit()
        item = self.candidates(limit=1)[0]
        self.assertEqual(item["shared_media"], 3)
        self.assertEqual(item["person_a"]["appearance_count"], 3)
        self.assertEqual(item["person_b"]["appearance_count"], 2)
        self.assertEqual(item["person_a"]["profile_face"]["id"], faces[0].id)
        self.assertEqual(item["person_a"]["profile_face"]["thumbnail_path"], "/fake/face.jpg")

    def test_decision_exclusion_happens_before_limit(self):
        self.decide(self.b, self.a)
        items = self.candidates(limit=1)
        self.assertEqual(
            [(item["person_a"]["id"], item["person_b"]["id"]) for item in items],
            [(self.b, self.c)],
        )

    def test_upsert_is_idempotent_in_both_orders_and_list_returns_rows(self):
        first = self.decide(self.b, self.a)
        self.assertEqual(first["person_a_id"], self.a)
        self.assertEqual(first["person_b_id"], self.b)
        self.assertEqual(first["decision"], "not_same")
        self.assertTrue(first["created_at"])
        self.assertEqual(self.decide(self.a, self.b), first)
        self.assertEqual(self.decide(self.b, self.a), first)
        response = self.client.get("/api/persons/pair-decisions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [first])
        self.assertEqual(len(self.session.exec(select(PersonPairDecision)).all()), 1)

    def test_delete_restores_candidate_and_unknown_decision_is_404(self):
        decision = self.decide(self.a, self.b)
        response = self.client.delete(f"/api/persons/pair-decisions/{decision['id']}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [])
        self.assertEqual(self.candidates(limit=1)[0]["similarity"], 80)
        self.assertEqual(self.client.delete(f"/api/persons/pair-decisions/{decision['id']}").status_code, 404)

    def test_invalid_decisions_and_missing_people_are_rejected(self):
        for a, b, decision, expected in [
            (self.a, self.a, "not_same", 400),
            (self.a, 9999, "not_same", 404),
            (9999, self.a, "not_same", 404),
            (self.a, self.b, "same", 422),
        ]:
            with self.subTest(a=a, b=b, decision=decision):
                response = self.client.post(
                    "/api/persons/pair-decisions",
                    json={"person_a_id": a, "person_b_id": b, "decision": decision},
                )
                self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [])

    def test_invalid_query_bounds_are_rejected(self):
        for params in ({"limit": 0}, {"limit": 201}, {"min_similarity": 101}, {"min_similarity": -101}, {"min_similarity": "nan"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/persons/merge-candidates", params=params).status_code, 422)

    def test_presentation_mode_blocks_mutations_but_allows_reads(self):
        decision = self.decide(self.a, self.b)
        settings.general.presentation_mode = True
        self.assertEqual(self.client.post(
            "/api/persons/pair-decisions",
            json={"person_a_id": self.a, "person_b_id": self.c, "decision": "not_same"},
        ).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/persons/pair-decisions/{decision['id']}").status_code, 403)
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [decision])
        self.assertEqual(len(self.candidates()), 2)

    def test_existing_merge_endpoint_cascades_source_decisions_only(self):
        self.decide(self.a, self.b)
        self.decide(self.a, self.c)
        survivor = self.decide(self.b, self.c)
        response = self.client.post("/api/person/merge", json={"source_id": self.a, "target_id": self.b})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.session.get(Person, self.a))
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [survivor])
        self.assertEqual(self.candidates(), [])

    def test_reverse_merge_cascades_decisions_where_source_is_second(self):
        self.decide(self.a, self.b)
        self.decide(self.b, self.c)
        survivor = self.decide(self.a, self.c)
        response = self.client.post("/api/person/merge", json={"source_id": self.b, "target_id": self.a})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [survivor])

    def test_person_delete_cascades_decisions(self):
        self.decide(self.a, self.b)
        response = self.client.delete(f"/api/person/{self.b}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.client.get("/api/persons/pair-decisions").json(), [])

    def test_model_constraints_reject_invalid_raw_writes(self):
        self.decide(self.a, self.b)
        for a, b, decision in [(self.a, self.b, "not_same"), (self.b, self.a, "not_same"), (self.a, self.a, "not_same"), (self.a, self.c, "same"), (self.a, 9999, "not_same")]:
            with self.subTest(a=a, b=b, decision=decision):
                with self.assertRaises(IntegrityError):
                    self.session.exec(text(
                        "INSERT INTO person_pair_decision(person_a_id, person_b_id, decision, created_at) "
                        "VALUES (:a, :b, :decision, CURRENT_TIMESTAMP)"
                    ).bindparams(a=a, b=b, decision=decision))
                    self.session.commit()
                self.session.rollback()

    def test_empty_embedding_table_returns_empty_items(self):
        self.session.exec(text("DELETE FROM person_embeddings"))
        self.session.commit()
        self.assertEqual(self.candidates(), [])

    def test_production_routes_mount_queue_before_person_id_routes(self):
        from app.main import app

        paths = list(app.openapi()["paths"])
        for path in ("/api/persons/merge-candidates", "/api/persons/pair-decisions", "/api/persons/pair-decisions/{id}"):
            self.assertIn(path, paths)
            self.assertLess(paths.index(path), paths.index("/api/person/{person_id}"))


class MergeQueueMigrationTests(unittest.TestCase):
    def test_migration_head_is_single_and_chained_from_previous_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["60718293a4b5"])
        self.assertEqual(script.get_revision("0a1b2c3d4e5f").down_revision, "f9a0b1c2d3e5")

    def test_migration_upgrade_constraints_cascade_and_downgrade(self):
        engine = create_engine("sqlite://")
        _attach_engine_listeners(engine)
        self.addCleanup(engine.dispose)
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0a1b2c3d4e5f").module
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE person (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO person(id) VALUES (1), (2), (3)")
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                schema = inspect(connection)
                self.assertEqual(
                    {tuple(fk["constrained_columns"]): fk["options"]["ondelete"] for fk in schema.get_foreign_keys("person_pair_decision")},
                    {("person_a_id",): "CASCADE", ("person_b_id",): "CASCADE"},
                )
                self.assertEqual(schema.get_unique_constraints("person_pair_decision")[0]["column_names"], ["person_a_id", "person_b_id"])
                self.assertEqual(len(schema.get_check_constraints("person_pair_decision")), 2)
                connection.exec_driver_sql(
                    "INSERT INTO person_pair_decision VALUES (1, 1, 2, 'not_same', CURRENT_TIMESTAMP), "
                    "(2, 2, 3, 'not_same', CURRENT_TIMESTAMP), (3, 1, 3, 'not_same', CURRENT_TIMESTAMP)"
                )
                with self.assertRaises(IntegrityError):
                    connection.exec_driver_sql("INSERT INTO person_pair_decision VALUES (4, 1, 2, 'not_same', CURRENT_TIMESTAMP)")
                with self.assertRaises(IntegrityError):
                    connection.exec_driver_sql("INSERT INTO person_pair_decision VALUES (4, 3, 2, 'not_same', CURRENT_TIMESTAMP)")
                with self.assertRaises(IntegrityError):
                    connection.exec_driver_sql("INSERT INTO person_pair_decision VALUES (4, 1, 2, 'same', CURRENT_TIMESTAMP)")
                connection.exec_driver_sql("DELETE FROM person WHERE id = 2")
                self.assertEqual(connection.exec_driver_sql("SELECT id FROM person_pair_decision").all(), [(3,)])
                revision.downgrade()
                self.assertNotIn("person_pair_decision", inspect(connection).get_table_names())


if __name__ == "__main__":
    unittest.main()
