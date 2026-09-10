"""Regression tests for person dependency preservation and timeline pagination."""
import importlib
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

_CONFIG = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG.name)

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select
from app.database import _attach_engine_listeners
from app.models import (Person, PersonSocialLink, TrainingDataset, PersonPairDecision,
                        TimelineEvent, Media, PersonMediaLink)
from app.schemas.person import SimilarPerson, PersonBulkDeleteRequest

api = importlib.import_module("app.api.person")
datasets = importlib.import_module("app.api.datasets")


class PersonSafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE VIRTUAL TABLE face_embeddings USING vec0(face_id integer primary key, person_id integer, embedding float[512])")
            conn.exec_driver_sql("CREATE VIRTUAL TABLE person_embeddings USING vec0(person_id integer, embedding float[512])")
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.a = Person(name="Source", appearance_count=0)
        self.b = Person(name="Target", appearance_count=0)
        self.session.add_all([self.a, self.b])
        self.session.commit()
        self.aid, self.bid = self.a.id, self.b.id

    def dataset(self, **kwargs):
        row = TrainingDataset(name="Subject", slug="subject", trigger_word="subject", class_token="person", **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def test_merge_preserves_and_deduplicates_social_links(self):
        self.session.add_all([
            PersonSocialLink(person_id=pid, platform="x", handle=handle, url="https://x.com/" + handle)
            for pid, handle in [(self.aid, "source"), (self.aid, "shared"), (self.bid, "shared")]
        ])
        self.session.commit()
        api._merge_person_into_target(self.session, self.aid, self.bid)
        self.assertEqual({(r.person_id, r.handle) for r in self.session.exec(select(PersonSocialLink))}, {(self.bid, "source"), (self.bid, "shared")})

    def test_merge_retargets_subject_dataset(self):
        dataset = self.dataset(person_id=self.aid)
        api._merge_person_into_target(self.session, self.aid, self.bid)
        self.session.refresh(dataset)
        self.assertEqual(dataset.person_id, self.bid)

    def test_delete_subject_dataset_person_is_explicit_conflict(self):
        self.dataset(person_id=self.aid)
        with self.assertRaises(HTTPException) as error:
            api.delete_person(self.aid, self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("dataset", error.exception.detail.lower())
        self.assertIsNotNone(self.session.get(Person, self.aid))

    def test_bulk_delete_preserves_subject_dataset_person(self):
        self.dataset(person_id=self.aid)
        result = api.delete_persons_bulk(PersonBulkDeleteRequest(person_ids=[self.aid]), self.session)
        self.assertEqual(result.skipped_ids, [self.aid])
        self.assertIsNotNone(self.session.get(Person, self.aid))

    def test_regularization_dependency_is_explicit_conflict(self):
        regularization = self.dataset()
        dependent = TrainingDataset(name="dependent", slug="dependent", trigger_word="x", class_token="person", regularization_dataset_id=regularization.id)
        self.session.add(dependent)
        self.session.commit()
        with self.assertRaises(HTTPException) as error:
            datasets.delete_dataset(regularization.id, self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("regularization", error.exception.detail.lower())

    def test_auto_merge_respects_saved_not_same(self):
        self.session.add(PersonPairDecision(person_a_id=self.aid, person_b_id=self.bid, decision="not_same"))
        self.session.commit()
        with patch.object(api, "get_similarities", return_value=[SimilarPerson(id=self.aid, name="Source", similarity=100)]), patch.object(api, "_merge_person_into_target") as merge:
            result = api.auto_merge_similar_persons(self.bid, self.session)
        merge.assert_not_called()
        self.assertEqual(result.skipped_ids, [self.aid])

    def test_auto_merge_rechecks_decision_between_candidates(self):
        third = Person(name="Third", appearance_count=0)
        self.session.add(third)
        self.session.commit()
        tid = third.id
        def first_merge(session, source, target):
            session.add(PersonPairDecision(person_a_id=self.bid, person_b_id=tid, decision="not_same"))
            session.commit()
        candidates = [SimilarPerson(id=pid, name="candidate", similarity=100) for pid in (self.aid, tid)]
        with patch.object(api, "get_similarities", return_value=candidates), patch.object(api, "_merge_person_into_target", side_effect=first_merge) as merge:
            result = api.auto_merge_similar_persons(self.bid, self.session)
        self.assertEqual(merge.call_count, 1)
        self.assertEqual(result.skipped_ids, [tid])

    def test_timeline_pages_every_same_day_media_and_event_once(self):
        for i in range(4):
            row = Media(path=f"/fake/{i}.jpg", filename=f"{i}.jpg", size=1, created_at=datetime(2026, 1, 2, 12))
            self.session.add(row)
            self.session.flush()
            self.session.add(PersonMediaLink(person_id=self.aid, media_id=row.id))
        self.session.add_all([TimelineEvent(person_id=self.aid, title=f"event{i}", event_date=date(2026, 1, 2)) for i in range(4)])
        self.session.commit()
        cursor = None
        keys = []
        for _ in range(10):
            page = api.get_person_timeline(self.aid, self.session, cursor=cursor, limit=3)
            keys.extend((r["type"], r.get("event", r.get("items")).id) for r in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(keys), 8)
        self.assertEqual(len(set(keys)), 8)

    def test_timeline_yearly_occurrence_does_not_repeat_on_same_day_pages(self):
        for i in range(6):
            self.session.add(TimelineEvent(person_id=self.aid, title=f"one-off{i}", event_date=date(2026, 1, 2)))
        yearly = TimelineEvent(person_id=self.aid, title="Birthday", event_date=date(2000, 1, 2), recurrence="yearly")
        self.session.add(yearly)
        self.session.commit()
        cursor = None
        occurrences = 0
        for _ in range(10):
            page = api.get_person_timeline(self.aid, self.session, cursor=cursor, limit=2)
            occurrences += sum(r["type"] == "event" and r["event"].id == yearly.id for r in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(occurrences, 1)

    def test_timeline_yearly_occurrence_survives_full_media_page_before_older_day(self):
        for i, day in enumerate((2, 2, 1)):
            row = Media(path=f"/fake/boundary-{i}.jpg", filename=f"{i}.jpg", size=1, created_at=datetime(2026, 1, day, 12))
            self.session.add(row)
            self.session.flush()
            self.session.add(PersonMediaLink(person_id=self.aid, media_id=row.id))
        yearly = TimelineEvent(person_id=self.aid, title="Birthday", event_date=date(2000, 1, 2), recurrence="yearly")
        self.session.add(yearly)
        self.session.commit()
        cursor = None
        rows = []
        for _ in range(5):
            page = api.get_person_timeline(self.aid, self.session, cursor=cursor, limit=2)
            self.assertLessEqual(len(page["items"]), 2)
            rows.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([(row["type"], row["date"]) for row in rows], [
            ("media", date(2026, 1, 2)), ("media", date(2026, 1, 2)),
            ("event", date(2026, 1, 2)), ("media", date(2026, 1, 1)),
        ])
        self.assertEqual(sum(row["type"] == "event" and row["event"].id == yearly.id for row in rows), 1)

    def test_timeline_cursor_rejects_integer_outside_sqlite_range(self):
        with self.assertRaises(HTTPException) as error:
            api.get_person_timeline(self.aid, self.session, cursor="2026-01-02|media|9223372036854775808", limit=2)
        self.assertEqual(error.exception.status_code, 422)

    def test_timeline_without_base_items_does_not_expand_yearly_events(self):
        self.session.add(TimelineEvent(person_id=self.aid, title="Birthday", event_date=date(2000, 1, 2), recurrence="yearly"))
        self.session.commit()
        self.assertEqual(api.get_person_timeline(self.aid, self.session, cursor=None, limit=2), {"items": [], "next_cursor": None})

    def test_timeline_yearly_occurrences_page_across_years_and_skip_invalid_leap_days(self):
        self.session.add_all([
            TimelineEvent(person_id=self.aid, title="Start", event_date=date(2023, 1, 1)),
            TimelineEvent(person_id=self.aid, title="End", event_date=date(2025, 12, 31)),
        ])
        leap = TimelineEvent(person_id=self.aid, title="Leap day", event_date=date(2000, 2, 29), recurrence="yearly")
        annual = TimelineEvent(person_id=self.aid, title="Annual", event_date=date(2000, 6, 1), recurrence="yearly")
        self.session.add_all([leap, annual])
        self.session.commit()
        cursor = None
        rows = []
        for _ in range(10):
            page = api.get_person_timeline(self.aid, self.session, cursor=cursor, limit=1)
            self.assertLessEqual(len(page["items"]), 1)
            rows.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([row["date"] for row in rows if row["event"].id == leap.id], [date(2024, 2, 29)])
        self.assertEqual([row["date"] for row in rows if row["event"].id == annual.id], [date(year, 6, 1) for year in (2025, 2024, 2023)])
        self.assertEqual([row["date"] for row in rows], sorted((row["date"] for row in rows), reverse=True))
        self.assertEqual(self.session.get(TimelineEvent, leap.id).event_date, date(2000, 2, 29))
