import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.database as db
from app.models import (
    DatasetItem,
    Face,
    Media,
    Person,
    ProcessingTask,
    Scene,
    TrainingDataset,
)
from app.services.frame_mining import (
    FrameCandidate,
    FrameMiner,
    candidate_timestamps,
    mine_candidates,
    score_candidates,
    select_candidates,
)
from app.services.media_files import MediaFileMissingError
from app.tasks.dataset_frame_mining import mine_dataset_frames


class FakeDetector:
    def __init__(self, faces):
        self.faces = faces

    def get(self, _image):
        return self.faces


def fake_face(embedding, bbox=(10, 10, 210, 210), nose_x=110):
    return SimpleNamespace(
        bbox=np.asarray(bbox, dtype=np.float32),
        normed_embedding=np.asarray(embedding, dtype=np.float32),
        kps=np.asarray(
            [[70, 70], [150, 70], [nose_x, 110], [80, 155], [140, 155]],
            dtype=np.float32,
        ),
    )


class FrameMiningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def video(self, name="source.avi", frames=6) -> Path:
        path = self.root / name
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"MJPG"), 4.0, (320, 240)
        )
        self.assertTrue(writer.isOpened())
        for index in range(frames):
            frame = np.full((240, 320, 3), (index * 35) % 255, dtype=np.uint8)
            writer.write(frame)
        writer.release()
        self.assertTrue(path.is_file())
        return path

    def records(self, session: Session, path: Path):
        person = Person(name="Subject", appearance_count=1)
        dataset = TrainingDataset(
            name="Frames",
            slug="frames",
            trigger_word="subject",
            class_token="person",
        )
        video = Media(
            path=str(path),
            filename=path.name,
            size=path.stat().st_size if path.exists() else 0,
            width=320,
            height=240,
            duration=1.5,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
        session.add_all([person, dataset, video])
        session.commit()
        dataset.person_id = person.id
        session.add(dataset)
        session.commit()
        return person, dataset, video

    def candidate(self, timestamp, sharpness, phash, yaw=0.0):
        return FrameCandidate(
            timestamp=timestamp,
            frame=np.full((240, 320, 3), round(timestamp * 30), dtype=np.uint8),
            likeness=0.9,
            bbox=[20, 20, 180, 180],
            yaw=yaw,
            pitch=0.0,
            sharpness=sharpness,
            face_size=180.0,
            phash=phash,
        )

    def test_candidate_timestamps_merge_scenes_and_faces_and_dedupe_250ms(self):
        with Session(self.engine) as session:
            person, _, video = self.records(session, self.video())
            session.add_all(
                [
                    Scene(media_id=video.id, start_time=0.0, end_time=1.0),
                    Scene(media_id=video.id, start_time=1.0, end_time=1.5),
                    Face(
                        media_id=video.id,
                        person_id=person.id,
                        bbox=[0, 0, 10, 10],
                        timestamp=0.1,
                    ),
                    Face(
                        media_id=video.id,
                        person_id=person.id,
                        bbox=[0, 0, 10, 10],
                        timestamp=1.2,
                    ),
                ]
            )
            session.commit()
            values = candidate_timestamps(
                session, video, person.id, fps=2.0, max_candidates=48
            )
            self.assertEqual(values, [0.0, 0.5, 1.0, 1.5])
            self.assertTrue(all(right - left >= 0.25 for left, right in zip(values, values[1:])))

    def test_candidate_timestamps_keep_face_moments_and_spread_across_long_scenes(self):
        with Session(self.engine) as session:
            person, _, video = self.records(session, self.video())
            video.duration = 100.0
            session.add(video)
            session.add_all(
                [
                    Scene(media_id=video.id, start_time=0.0, end_time=100.0),
                    Face(media_id=video.id, person_id=person.id, bbox=[0, 0, 10, 10], timestamp=17.2),
                    Face(media_id=video.id, person_id=person.id, bbox=[0, 0, 10, 10], timestamp=90.0),
                ]
            )
            session.commit()
            values = candidate_timestamps(
                session, video, person.id, fps=2.0, max_candidates=48
            )
            self.assertEqual(len(values), 48)
            self.assertTrue(any(abs(value - 17.2) <= 0.25 for value in values))
            self.assertIn(90.0, values)
            self.assertGreater(max(values), 95.0)
            self.assertLess(min(values), 1.0)
            self.assertEqual(values, sorted(values))

    def test_identity_filter_keeps_only_matching_faces(self):
        miner = FrameMiner.__new__(FrameMiner)
        miner.processor = SimpleNamespace(
            _estimate_pose=lambda _kps: (0.0, 0.0)
        )
        miner.detector = FakeDetector(
            [fake_face([0, 1]), fake_face([1, 0], bbox=(20, 20, 240, 230))]
        )
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        candidate = miner.candidate_from_frame(
            frame, 0.5, np.asarray([1, 0], dtype=np.float32), min_face_px=100
        )
        self.assertIsNotNone(candidate)
        self.assertAlmostEqual(candidate.likeness, 1.0)
        self.assertEqual(candidate.bbox, [20, 20, 220, 210])

        miner.detector = FakeDetector([fake_face([0, 1])])
        self.assertIsNone(
            miner.candidate_from_frame(
                frame, 0.5, np.asarray([1, 0], dtype=np.float32), min_face_px=100
            )
        )

    def test_opencv_seek_decodes_synthetic_video_with_patched_detector(self):
        miner = FrameMiner.__new__(FrameMiner)
        miner.processor = SimpleNamespace(
            _estimate_pose=lambda _kps: (0.0, 0.0)
        )
        miner.detector = FakeDetector([fake_face([1, 0])])
        candidates = miner.decode_candidates(
            self.video(),
            [0.0, 0.75],
            np.asarray([1, 0], dtype=np.float32),
            min_face_px=100,
        )
        self.assertEqual([candidate.timestamp for candidate in candidates], [0.0, 0.75])
        self.assertNotEqual(float(candidates[0].frame.mean()), float(candidates[1].frame.mean()))

    def test_scoring_prefers_sharp_and_novel_frames_and_selection_honours_limit(self):
        duplicate = self.candidate(0.0, 100.0, "0000000000000000")
        novel = self.candidate(0.5, 100.0, "ffffffffffffffff", yaw=45.0)
        soft = self.candidate(1.0, 5.0, "eeeeeeeeeeeeeeee", yaw=-45.0)
        score_candidates(
            [duplicate, novel, soft],
            [(0.0, 0.0, "0000000000000000")],
        )
        self.assertGreater(novel.score, duplicate.score)
        self.assertGreater(novel.score, soft.score)
        selected = select_candidates([duplicate, novel, soft], max_per_video=2)
        self.assertEqual(len(selected), 2)
        self.assertIn(novel, selected)

    def test_task_writes_registers_origin_and_queues_processing(self):
        path = self.video()
        with Session(self.engine) as session:
            _, dataset, video = self.records(session, path)
            task = ProcessingTask(task_type="dataset_frame_mining")
            session.add(task)
            session.commit()
            task_id = task.id
            dataset_id = dataset.id
            video_id = video.id
        mined = self.candidate(0.5, 42.0, "0123456789abcdef")
        with (
            patch.object(db, "engine", self.engine),
            patch(
                "app.config.GeneralSettings.resolved_media_dirs",
                return_value=[(self.root, False)],
            ),
            patch(
                "app.tasks.dataset_frame_mining.mine_candidates",
                return_value=[mined],
            ),
            patch(
                "app.tasks.dataset_frame_mining.generate_thumbnail",
                return_value=("media/frame.jpg", None),
            ),
            patch(
                "app.tasks.media_processing.edit_processor_names",
                return_value=["faces"],
            ),
            patch("app.tasks.media_processing.run_processors_for_media") as queued,
        ):
            mine_dataset_frames(
                task_id,
                dataset_id=dataset_id,
                video_media_ids=[video_id],
                max_per_video=1,
                min_face_px=1,
                fps=2,
            )

        target = self.root / "source_frame-500.jpg"
        self.assertTrue(target.is_file())
        with Session(self.engine) as session:
            media = session.exec(select(Media).where(Media.path == str(target))).one()
            item = session.exec(select(DatasetItem).where(DatasetItem.media_id == media.id)).one()
            self.assertEqual(item.origin, "frame")
            self.assertEqual(item.dataset_id, dataset_id)
            self.assertEqual(media.created_at, datetime(2026, 1, 1, 12, 0, 0, 500000))
        queued.assert_called_once()

    def test_missing_video_is_refused_before_decoding(self):
        missing = self.root / "missing.avi"
        with Session(self.engine) as session:
            _, dataset, video = self.records(session, missing)
            with (
                patch(
                    "app.config.GeneralSettings.resolved_media_dirs",
                    return_value=[(self.root, False)],
                ),
                patch("app.services.frame_mining.person_centroid") as centroid,
            ):
                with self.assertRaises(MediaFileMissingError):
                    mine_candidates(session, dataset, video)
                centroid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
