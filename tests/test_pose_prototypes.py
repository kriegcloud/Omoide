import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import numpy as np

import app.api  # noqa: F401  # Bootstrap tasks in application import order.
from app.config import settings
from app.tasks.person_clustering import (
    _load_person_prototype_matrix,
    _match_unassigned_to_existing,
)
from app.utils import vector_to_blob


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _PrototypeSession:
    def __init__(self, rows):
        self._rows = rows

    def exec(self, _statement):
        return _RowsResult(self._rows)


class PosePrototypeTests(unittest.TestCase):
    def setUp(self):
        self.a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        self.b = np.array(
            [0.3, np.sqrt(1.0 - 0.3**2), 0.0], dtype=np.float32
        )
        self.rows = [
            (1, 0.8, vector_to_blob(self.a), 1) for _ in range(40)
        ] + [(1, 0.1, vector_to_blob(self.b), 0) for _ in range(6)]

    def _load(self, *, enabled=True, rows=None, sample_cap=8):
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    settings.face_recognition,
                    "person_pose_prototypes_enabled",
                    enabled,
                )
            )
            stack.enter_context(
                patch.object(
                    settings.face_recognition,
                    "person_pose_prototype_min_faces",
                    4,
                )
            )
            stack.enter_context(
                patch.object(
                    settings.face_recognition,
                    "person_pose_prototype_sample_cap",
                    sample_cap,
                )
            )
            return _load_person_prototype_matrix(
                _PrototypeSession(self.rows if rows is None else rows),
                per_person_cap=sample_cap,
            )

    def test_profile_pose_prototype_is_added_only_when_enabled(self):
        person_ids, matrix = self._load(enabled=True)
        self.assertTrue(np.all(person_ids == 1))
        np.testing.assert_allclose(
            np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-6, atol=1e-6
        )
        self.assertGreater(float(np.max(matrix @ self.b)), 0.95)

        _, old_matrix = self._load(enabled=False)
        self.assertLess(float(np.max(old_matrix @ self.b)), 0.95)

    def test_pose_bin_below_minimum_face_count_is_skipped(self):
        rows = self.rows[:40] + self.rows[40:43]
        _, matrix = self._load(enabled=True, rows=rows)
        self.assertLess(float(np.max(matrix @ self.b)), 0.95)

    def test_sampling_is_deterministic(self):
        first_ids, first_matrix = self._load(enabled=True)
        second_ids, second_matrix = self._load(enabled=True)
        np.testing.assert_array_equal(first_ids, second_ids)
        np.testing.assert_array_equal(first_matrix, second_matrix)

    def test_profile_face_matches_only_with_pose_prototype(self):
        u_second = (0.7 - (0.35 * 0.3)) / self.b[1]
        profile_face = np.array(
            [0.35, u_second, np.sqrt(1.0 - 0.35**2 - u_second**2)],
            dtype=np.float32,
        )
        new_prototypes = self._load(enabled=True)
        old_prototypes = self._load(enabled=False)

        def match(prototypes):
            session = MagicMock()
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        settings.face_recognition,
                        "existing_person_cosine_threshold",
                        0.62,
                    )
                )
                stack.enter_context(
                    patch.object(
                        settings.face_recognition,
                        "existing_person_min_cosine_margin",
                        0.05,
                    )
                )
                stack.enter_context(
                    patch(
                        "app.tasks.person_clustering._is_task_cancelled",
                        return_value=False,
                    )
                )
                bulk_assign = stack.enter_context(
                    patch("app.tasks.person_clustering._bulk_assign_faces_to_persons")
                )
                stack.enter_context(
                    patch("app.tasks.person_clustering.safe_commit")
                )
                stack.enter_context(
                    patch("app.tasks.person_clustering.set_task_progress")
                )
                remaining = _match_unassigned_to_existing(
                    session,
                    [99],
                    profile_face.reshape(1, -1),
                    "pose-prototype-test",
                    prototypes=prototypes,
                )
            return remaining, bulk_assign

        remaining, bulk_assign = match(new_prototypes)
        self.assertEqual(remaining, [])
        bulk_assign.assert_called_once()
        self.assertEqual(bulk_assign.call_args.args[1], {99: 1})

        remaining, bulk_assign = match(old_prototypes)
        self.assertEqual(remaining, [99])
        bulk_assign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
