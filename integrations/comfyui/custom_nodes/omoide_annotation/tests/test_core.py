from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


NODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NODE_ROOT))

from core import (  # noqa: E402
    CATEGORY_CHARACTER,
    CATEGORY_GENERAL,
    CATEGORY_RATING,
    CHARACTER_THRESHOLD,
    GENERAL_THRESHOLD,
    LABEL_FILENAME,
    MODEL_FILENAME,
    TagLabel,
    build_documents,
    canonical_json,
    load_labels,
    load_model_bundle,
    prepare_batch,
    prepare_image,
    preview_text,
    run_session,
    verify_artifact,
)


class FakeSession:
    def __init__(self, result: np.ndarray) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, np.ndarray]]] = []

    def run(self, output_names, input_feed):
        self.calls.append((list(output_names), input_feed))
        return [self.result]


class FakeModelSession:
    def get_inputs(self):
        return [types.SimpleNamespace(name="input", shape=[None, 2, 2, 3])]

    def get_outputs(self):
        return [types.SimpleNamespace(name="output", shape=[None, 3])]


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.labels = (
            TagLabel(0, 9999999, "general", CATEGORY_RATING),
            TagLabel(1, 1, "blue_hair", CATEGORY_GENERAL),
            TagLabel(2, 2, "solo", CATEGORY_GENERAL),
            TagLabel(3, 3, "some_character", CATEGORY_CHARACTER),
        )

    def test_load_labels_preserves_model_output_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected_tags.csv"
            path.write_text(
                "tag_id,name,category,count\n"
                "9,rating,9,1\n"
                "3,a_tag,0,1\n"
                "7,a_character,4,1\n",
                encoding="utf-8",
            )
            labels = load_labels(path)
        self.assertEqual([label.output_index for label in labels], [0, 1, 2])
        self.assertEqual([label.tag_id for label in labels], [9, 3, 7])

    def test_preprocess_pads_white_and_converts_rgb_to_bgr(self) -> None:
        image = np.zeros((1, 2, 3), dtype=np.float32)
        image[0, 0] = (1.0, 0.0, 0.0)
        prepared = prepare_image(image, target_size=2)
        self.assertEqual(prepared.shape, (2, 2, 3))
        self.assertEqual(prepared.dtype, np.float32)
        np.testing.assert_array_equal(prepared[0, 0], [0.0, 0.0, 255.0])
        np.testing.assert_array_equal(prepared[1, 0], [255.0, 255.0, 255.0])

    def test_prepare_batch_supports_multiple_images(self) -> None:
        images = np.stack(
            [
                np.zeros((2, 2, 3), dtype=np.float32),
                np.ones((2, 2, 3), dtype=np.float32),
            ]
        )
        prepared = prepare_batch(images, target_size=2)
        self.assertEqual(prepared.shape, (2, 2, 2, 3))

    def test_document_has_exact_shape_and_preserves_all_scores(self) -> None:
        scores = np.array(
            [
                [
                    0.70,
                    GENERAL_THRESHOLD + 0.01,
                    GENERAL_THRESHOLD,
                    CHARACTER_THRESHOLD + 0.01,
                ],
                [0.40, 0.10, 0.99, 0.01],
            ],
            dtype=np.float32,
        )
        documents = build_documents(scores, self.labels)

        self.assertEqual(len(documents), 2)
        first = documents[0]
        self.assertEqual(
            set(first),
            {"schema", "kind", "profile_id", "model", "tags"},
        )
        self.assertEqual(first["schema"], "omoide.annotation/v1")
        self.assertEqual(first["kind"], "tags")
        self.assertEqual(first["profile_id"], "omoide-tags-v1")
        self.assertNotIn("training_tags", first)
        self.assertEqual(
            sum(len(values) for values in first["tags"].values()),
            len(self.labels),
        )
        self.assertEqual(
            first["tags"]["general"],
            [
                {"name": "blue_hair", "score": float(scores[0, 1])},
                {"name": "solo", "score": float(scores[0, 2])},
            ],
        )
        self.assertEqual(first["tags"]["character"][0]["name"], "some_character")

    def test_preview_uses_the_exact_emitted_float32_thresholds(self) -> None:
        above_general = float(
            np.nextafter(np.float32(GENERAL_THRESHOLD), np.float32(1.0))
        )
        scores = np.array(
            [[0.70, GENERAL_THRESHOLD, above_general, CHARACTER_THRESHOLD]],
            dtype=np.float32,
        )
        document = build_documents(scores, self.labels)[0]

        self.assertEqual(document["model"]["general_threshold"], GENERAL_THRESHOLD)
        self.assertEqual(
            document["model"]["character_threshold"], CHARACTER_THRESHOLD
        )
        preview = preview_text([document])
        self.assertIn("general tags=solo", preview)
        self.assertNotIn("blue_hair", preview)
        self.assertIn("character candidates=0", preview)

    def test_canonical_json_is_stable_and_compact(self) -> None:
        document = build_documents(
            np.array([[0.7, 0.9, 0.1, 0.2]], dtype=np.float32),
            self.labels,
        )[0]
        first = canonical_json(document)
        second = canonical_json(document)
        self.assertEqual(first, second)
        self.assertNotIn(" ", first)
        self.assertTrue(first.startswith('{"kind":"tags"'))

    def test_run_session_uses_only_named_input_and_output(self) -> None:
        expected = np.array([[0.5]], dtype=np.float32)
        session = FakeSession(expected)
        images = np.zeros((1, 2, 2, 3), dtype=np.float32)
        actual = run_session(session, "input", "output", images)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(session.calls[0][0], ["output"])
        self.assertIs(session.calls[0][1]["input"], images)

    def test_artifact_checksum_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            payload = b"reviewed artifact"
            path.write_bytes(payload)
            verify_artifact(path, hashlib.sha256(payload).hexdigest())
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_artifact(path, "0" * 64)

    def test_model_paths_and_runtime_are_hard_pinned(self) -> None:
        sessions: list[tuple[str, list[str]]] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / LABEL_FILENAME).write_text(
                "tag_id,name,category,count\n"
                "9,rating,9,1\n"
                "3,a_tag,0,1\n"
                "7,a_character,4,1\n",
                encoding="utf-8",
            )
            (root / MODEL_FILENAME).write_bytes(b"fake")

            fake_ort = types.ModuleType("onnxruntime")

            def fake_inference_session(path, *, providers):
                sessions.append((path, providers))
                return FakeModelSession()

            fake_ort.InferenceSession = fake_inference_session

            load_model_bundle.cache_clear()
            with (
                patch.dict(
                    sys.modules,
                    {"onnxruntime": fake_ort},
                ),
                patch("core.verify_artifact") as verify,
            ):
                bundle = load_model_bundle(root)
                self.assertEqual(verify.call_count, 2)
            load_model_bundle.cache_clear()

        self.assertEqual(sessions[0][0], root / MODEL_FILENAME)
        self.assertEqual(sessions[0][1], ["CPUExecutionProvider"])
        self.assertEqual(bundle.target_size, 2)


if __name__ == "__main__":
    unittest.main()
