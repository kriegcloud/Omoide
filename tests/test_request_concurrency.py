"""Exercise real router dispatch without TestClient, sockets, or the live catalog."""
import asyncio
import importlib
import os
import math
import statistics
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_CONFIG = tempfile.TemporaryDirectory(prefix="omoide-concurrency-config-")
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG.name)

import app.api  # noqa: E402,F401
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import Face, Media, Person  # noqa: E402

face = importlib.import_module("app.api.face")
config_api = importlib.import_module("app.api.config")


def run_async(coroutine):
    # Keep a timer pending: restricted runners may not deliver thread wakeups
    # through asyncio's socketpair. This does not move handler work off-loop.
    async def run():
        async def heartbeat():
            while True:
                await asyncio.sleep(0.005)
        timer = asyncio.create_task(heartbeat())
        try:
            return await asyncio.wait_for(coroutine, timeout=10)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
    return asyncio.run(run())


class RequestConcurrencyTests(unittest.TestCase):
    delay = 0.25

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omoide-request-concurrency-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            f"sqlite:///{self.root / 'fixture.db'}", connect_args={"check_same_thread": False},
            pool_size=2, max_overflow=0,
        )
        _attach_engine_listeners(self.engine)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE VIRTUAL TABLE face_embeddings USING vec0("
                                       "face_id integer primary key, person_id integer, embedding float[512])")
            connection.exec_driver_sql("CREATE VIRTUAL TABLE person_embeddings USING vec0("
                                       "person_id integer primary key, embedding float[512])")
        with Session(self.engine) as session:
            person = Person(name="Fixture", appearance_count=0)
            media = Media(path=str(self.root / "fixture.jpg"), filename="fixture.jpg", size=1)
            session.add_all([person, media])
            session.flush()
            orphan = Face(media_id=media.id, bbox=[0, 0, 10, 10])
            session.add(orphan)
            session.flush()
            self.person_id, self.face_id = person.id, orphan.id
            session.connection().exec_driver_sql(
                "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (?, -1, ?)",
                (orphan.id, b'\x00\x00\x80\x3f' + b'\x00' * (511 * 4)),
            )
            session.commit()
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.app = FastAPI()
        self.app.include_router(face.router, prefix="/faces")
        self.app.include_router(config_api.router, prefix="/config")

        self.session_threads = []

        class RequestSession(Session):
            # A whole handler owns all ORM/vector/commit work; dependency setup
            # and teardown may run on other idle FastAPI worker threads.
            def exec(owned, *args, **kwargs):
                owned.work_threads.add(threading.get_ident())
                return super().exec(*args, **kwargs)

            def get(owned, *args, **kwargs):
                owned.work_threads.add(threading.get_ident())
                return super().get(*args, **kwargs)

            def commit(owned):
                owned.work_threads.add(threading.get_ident())
                return super().commit()

        def sessions():
            with RequestSession(self.engine) as session:
                session.work_threads = set()
                self.session_threads.append(session.work_threads)
                yield session
        self.app.dependency_overrides[get_session] = sessions

        @self.app.get("/health")
        async def health():
            return {"status": "ok"}

    async def exercise(self, path, payload, started):
        """Schedule probes *before* work so an event-loop stall counts as latency."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="http://fixture") as client:
            scheduled = time.perf_counter()
            mutation = asyncio.create_task(client.post(path, json=payload))
            # Poll an event without blocking the event loop. If the handler blocks,
            # this task cannot observe the event until after the injected sleep.
            while not started.is_set() and not mutation.done():
                await asyncio.sleep(0.001)
            probe_started = time.perf_counter()
            responses = await asyncio.gather(client.get("/health"), client.get("/faces/orphans/count"))
            elapsed = time.perf_counter() - scheduled
            probe_time = time.perf_counter() - probe_started
            completed_early = not mutation.done()
            response = await mutation
            self.assertTrue(all(r.status_code == 200 for r in responses))
            self.assertEqual(responses[0].json(), {"status": "ok"})
            print(f"CONCURRENCY {path}: scheduled_probe_ms={elapsed * 1000:.2f} "
                  f"http_probe_ms={probe_time * 1000:.2f} mutation_pending={completed_early}")
            self.assertLess(elapsed, self.delay * 0.8, "lightweight GET was held behind synchronous work")
            self.assertTrue(completed_early, "GET must finish while slow mutation is still running")
            return response

    def test_face_mutations_leave_gets_responsive(self):
        cases = (
            ("/faces/assign", {"face_ids": [self.face_id], "person_id": self.person_id}, 200),
            ("/faces/assign-suggested", {"assignments": [{"face_id": self.face_id, "person_id": self.person_id}]}, 200),
            ("/faces/detach", {"face_ids": [self.face_id]}, 200),
            ("/faces/create_person", {"face_ids": [self.face_id], "name": "New fixture"}, 201),
        )
        for path, payload, expected_status in cases:
            with self.subTest(path=path):
                with Session(self.engine) as session:
                    person = session.get(Person, self.person_id)
                    person.profile_face_id = None
                    session.add(person)
                    row = session.get(Face, self.face_id)
                    row.person_id = None
                    session.add(row)
                    session.commit()
                started = threading.Event()
                sql_threads = set()
                loop_thread = threading.get_ident()

                def delayed_vector(connection, cursor, statement, parameters, context, executemany):
                    if "face_embeddings" in statement and statement.lstrip().upper().startswith("UPDATE"):
                        sql_threads.add(threading.get_ident())
                        started.set()
                        time.sleep(self.delay)
                event.listen(self.engine, "before_cursor_execute", delayed_vector)
                try:
                    response = run_async(self.exercise(path, payload, started))
                finally:
                    event.remove(self.engine, "before_cursor_execute", delayed_vector)
                self.assertEqual(response.status_code, expected_status, response.text)
                self.assertEqual(len(sql_threads), 1)
                self.assertNotIn(loop_thread, sql_threads)
                for threads in self.session_threads:
                    self.assertEqual(len(threads), 1)
                    self.assertNotIn(loop_thread, threads)
                with Session(self.engine) as session:
                    row = session.get(Face, self.face_id)
                    self.assertEqual(row.person_id, None if path.endswith("detach") else
                                     response.json()["id"] if path.endswith("create_person") else self.person_id)
                    self.assertIsNotNone(row.assigned_at)

    def test_config_save_leaves_gets_responsive_and_holds_guard(self):
        started = threading.Event()
        observed = []

        def slow_save(incoming):
            observed.append((threading.get_ident(), config_api._profile_change_lock.locked()))
            started.set()
            time.sleep(self.delay)

        with patch.object(config_api, "_removed_media_dirs", return_value=[]), \
             patch.object(config_api, "validate_clip_settings_change"), \
             patch.object(config_api, "save_settings", side_effect=slow_save):
            response = run_async(self.exercise("/config/", settings.model_dump(mode="json"), started))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), settings.model_dump(mode="json"))
        self.assertTrue(observed[0][1])
        self.assertNotEqual(observed[0][0], threading.get_ident())
        self.assertFalse(config_api._profile_change_lock.locked())

    def test_config_reload_leaves_gets_responsive_and_preserves_conflicts(self):
        started = threading.Event()
        observed = []

        def slow_reload():
            observed.append(threading.get_ident())
            started.set()
            time.sleep(self.delay)

        async def exercise_reload():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="http://fixture") as client:
                pending = asyncio.create_task(self.exercise("/config/reload", None, started))
                while not started.is_set() and not pending.done():
                    await asyncio.sleep(0.001)
                reads = await client.get("/config/")
                overlap = await client.post("/config/reload")
                response = await pending
                self.assertEqual(reads.status_code, 503)
                self.assertEqual(overlap.status_code, 409)
                return response

        with patch.object(config_api, "_reload_and_reconfigure", side_effect=slow_reload):
            response = run_async(exercise_reload())
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(len(observed), 1)
        self.assertNotEqual(observed[0], threading.get_ident())
        self.assertFalse(config_api._profile_change_lock.locked())

    def test_config_guard_releases_after_worker_failure(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                         base_url="http://fixture") as client:
                with patch.object(config_api, "_reload_and_reconfigure", side_effect=RuntimeError("fixture failure")):
                    failed = await client.post("/config/reload")
                self.assertEqual(failed.status_code, 500)
                self.assertFalse(config_api._profile_change_lock.locked())
                with patch.object(config_api, "_reload_and_reconfigure"):
                    self.assertEqual((await client.post("/config/reload")).status_code, 204)
        run_async(run())


    def test_probe_latency_with_one_busy_writer(self):
        started, release = threading.Event(), threading.Event()
        occupancy = []
        latencies = []

        def hold_vector(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE FACE_EMBEDDINGS"):
                started.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("fixture probes did not release writer")

        def checkout(*args):
            occupancy.append(self.engine.pool.checkedout())

        async def measure():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="http://fixture") as client:
                mutation = asyncio.create_task(client.post("/faces/assign", json={
                    "face_ids": [self.face_id], "person_id": self.person_id,
                }))
                try:
                    while not started.is_set() and not mutation.done():
                        await asyncio.sleep(0.001)
                    self.assertTrue(started.is_set())
                    # Two lightweight requests at a time, alongside one writer.
                    for _ in range(10):
                        begin = time.perf_counter()
                        results = await asyncio.gather(client.get("/health"), client.get("/faces/orphans/count"))
                        latencies.append((time.perf_counter() - begin) * 1000)
                        self.assertTrue(all(r.status_code == 200 for r in results))
                        self.assertFalse(mutation.done())
                finally:
                    release.set()
                    response = await mutation
                self.assertEqual(response.status_code, 200, response.text)

        event.listen(self.engine, "before_cursor_execute", hold_vector)
        event.listen(self.engine, "checkout", checkout)
        try:
            run_async(measure())
        finally:
            event.remove(self.engine, "before_cursor_execute", hold_vector)
            event.remove(self.engine, "checkout", checkout)
        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1]
        print(f"PROBE_BUDGET n={len(latencies)} read_concurrency=2 writers=1 "
              f"pool_limit=2 max_checked_out={max(occupancy)} p50_ms={p50:.2f} p95_ms={p95:.2f}")
        self.assertLess(p95, 200)
        self.assertEqual(max(occupancy), 2)
