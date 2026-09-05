import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.annotation_coordination import (
    MEDIA_ANNOTATION_MUTATION_LOCK,
    lock_media_annotation_mutation,
)
from app.annotation_tasks import (
    AnnotationMediaUnavailable,
    create_annotation_attempt,
)
from app.models import AnnotationAttempt, AnnotationAttemptStatus, AnnotationKind, Media
from app.utils import delete_record


class AnnotationMediaCoordinationTests(unittest.TestCase):
    @staticmethod
    def _engine(root: str):
        database_path = Path(root) / "coordination.sqlite"
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 2},
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(
                Media(
                    id=1,
                    path="/fixture/rights-cleared.jpg",
                    filename="rights-cleared.jpg",
                    size=1234,
                )
            )
            seed.commit()
        return engine

    def test_delete_lease_wins_before_attempt_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = self._engine(temporary)

            outcome: list[str] = []

            def admit() -> None:
                with Session(engine) as worker:
                    try:
                        create_annotation_attempt(
                            worker,
                            media_id=1,
                            kind=AnnotationKind.TAGS,
                        )
                    except AnnotationMediaUnavailable:
                        outcome.append("media-unavailable")
                    else:  # pragma: no cover - would violate serialization
                        outcome.append("admitted")

            with MEDIA_ANNOTATION_MUTATION_LOCK:
                with Session(engine) as deleting:
                    self.assertTrue(lock_media_annotation_mutation(deleting, 1))
                    worker = threading.Thread(target=admit)
                    worker.start()
                    time.sleep(0.05)
                    self.assertTrue(worker.is_alive())
                    deleting.delete(deleting.get(Media, 1))
                    deleting.commit()
            worker.join(timeout=2)

            self.assertEqual(outcome, ["media-unavailable"])
            with Session(engine) as verify:
                self.assertEqual(
                    list(verify.exec(select(AnnotationAttempt)).all()),
                    [],
                )
            engine.dispose()

    def test_database_write_lease_serializes_external_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = self._engine(temporary)
            outcome: list[bool] = []

            def acquire_after_delete() -> None:
                with Session(engine) as contender:
                    outcome.append(lock_media_annotation_mutation(contender, 1))
                    contender.rollback()

            with Session(engine) as deleting:
                self.assertTrue(lock_media_annotation_mutation(deleting, 1))
                worker = threading.Thread(target=acquire_after_delete)
                worker.start()
                time.sleep(0.05)
                self.assertTrue(worker.is_alive())
                deleting.delete(deleting.get(Media, 1))
                deleting.commit()
            worker.join(timeout=2)

            self.assertEqual(outcome, [False])
            engine.dispose()

    def test_unresolved_attempt_blocks_media_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = self._engine(temporary)
            with Session(engine) as session:
                attempt = create_annotation_attempt(
                    session,
                    media_id=1,
                    kind=AnnotationKind.TAGS,
                )
                attempt.status = AnnotationAttemptStatus.UNKNOWN
                attempt.retryable = False
                session.add(attempt)
                session.commit()

                with self.assertRaises(HTTPException) as raised:
                    delete_record(1, session)

                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(raised.exception.detail["attempt_id"], attempt.id)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
