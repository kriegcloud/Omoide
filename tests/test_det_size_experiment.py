from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from PIL import Image

from scripts import experiment_det_size as experiment
from app.services.face_working_image import face_scene_rgb, face_working_image


def face(box=(1, 2, 11, 22), embedding=None):
    return SimpleNamespace(bbox=box, det_score=.9, embedding=embedding)


def run(faces, seconds=.1):
    details = [experiment.face_record(value) for value in faces]
    return {"faces_found": len(details), "faces": details, "seconds": seconds}


class DetectorSizeExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def database(self):
        path = self.root / "omoide.db"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("CREATE TABLE media(id INTEGER PRIMARY KEY, path TEXT, duration REAL, faces_extracted INTEGER, processing_error TEXT); CREATE TABLE face(media_id INTEGER);")
            connection.executemany("INSERT INTO media VALUES (?, ?, NULL, 1, NULL)", ((index, f"/private/image-{index}.jpg") for index in range(1, 621)))
            connection.executemany("INSERT INTO face VALUES (?)", ((index,) for index in range(1, 311)))
            # Multiple faces must not weight a media id more heavily.
            connection.executemany("INSERT INTO face VALUES (1)", [()] * 40)
            connection.execute("UPDATE media SET duration=3 WHERE id=1")
            connection.execute("UPDATE media SET faces_extracted=0 WHERE id=2")
            connection.execute("UPDATE media SET processing_error='broken' WHERE id=3")
        return path

    def test_sampling_is_balanced_deterministic_eligible_and_read_only(self):
        path = self.database()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with experiment.readonly_database(path) as connection:
            sample = experiment.sample_media(connection)
            self.assertEqual(sample, experiment.sample_media(connection))
            self.assertEqual(len(sample), 500)
            self.assertEqual(len({row.media_id for row in sample}), 500)
            self.assertEqual(sum(row.had_faces for row in sample), 250)
            self.assertFalse({1, 2, 3} & {row.media_id for row in sample})
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM media")
            connection.execute("PRAGMA query_only=OFF")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM media")
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn("/private", repr(sample))

    def test_insufficient_stratum_fails_instead_of_replacing_sample(self):
        with experiment.readonly_database(self.database()) as connection:
            with self.assertRaisesRegex(experiment.ExperimentError, "Insufficient eligible"):
                experiment.sample_media(connection, per_stratum=400)

    def test_readonly_does_not_create_missing_database(self):
        path = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            with experiment.readonly_database(path):
                pass
        self.assertFalse(path.exists())

    def test_shared_preparation_matches_old_pipeline_rounding_rgb_and_exif(self):
        image = Image.new("RGB", (1601, 803), (10, 50, 90))
        image.getexif()[274] = 6
        rgb = face_scene_rgb(image)
        self.assertEqual(rgb.shape, (1601, 803, 3))
        np.testing.assert_array_equal(rgb[0, 0], [10, 50, 90])
        expected = cv2.resize(rgb, (int(803 * 1280 / 1601), 1280), interpolation=cv2.INTER_AREA)
        np.testing.assert_array_equal(face_working_image(rgb), expected)
        small = np.zeros((20, 30, 3), dtype=np.uint8)
        self.assertIs(face_working_image(small), small)

    def test_only_sample_sources_are_opened_and_root_mapping_has_boundary(self):
        image_path = self.root / "selected.png"
        Image.new("RGB", (1600, 800)).save(image_path)
        row = experiment.Sample(4, False, "/app/media/T7/selected.png")
        mapping = [(Path("/app/media/T7"), self.root)]
        real_open = Image.open
        with patch.object(Image, "open", wraps=real_open) as opened:
            image = experiment.prepare_sample(row, mapping)
        opened.assert_called_once_with(image_path)
        self.assertEqual(image.shape[:2], (640, 1280))
        with self.assertRaises(experiment.ExperimentError):
            experiment.source_path(experiment.Sample(5, False, "/app/media/T7other/file.jpg"), mapping)
        self.assertEqual(experiment.source_path(experiment.Sample(5, False, "/ordinary/one.jpg"), mapping), Path("/ordinary/one.jpg"))

    def test_run_uses_same_array_alternates_order_and_records_errors_without_paths(self):
        calls = []
        image = np.zeros((10, 20, 3), np.uint8)
        def get(size):
            def invoke(actual):
                self.assertIs(actual, image)
                calls.append(size)
                if len(calls) == 4:
                    raise ValueError("sensitive /private/path.jpg")
                return [face(embedding=[1, 0])]
            return invoke
        clients = {size: SimpleNamespace(get=get(size)) for size in experiment.SIZES}
        rows = [experiment.Sample(1, True, "private"), experiment.Sample(2, False, "private")]
        records = experiment.run_images(rows, clients, lambda _: image, warmup=False)
        self.assertEqual(calls, [640, 1280, 1280, 640])
        self.assertEqual(records[0]["runs"]["640"]["faces"][0]["area"], 200)
        self.assertTrue(records[0]["runs"]["640"]["faces"][0]["embedding_produced"])
        self.assertEqual(records[1]["runs"]["640"]["error"], "ValueError")
        self.assertNotIn("private", json.dumps(records))
        report = experiment.report_markdown(records, [], dry_run=False)
        self.assertIn("Complete pairs: 1. Excluded incomplete pairs: 1", report)
        self.assertIn("| Faces found | 1 | 1 |", report)

    def test_prepare_failure_never_becomes_zero_faces(self):
        def fail(_):
            raise OSError("/private/source.png")
        records = experiment.run_images([experiment.Sample(9, False, "private")], {}, fail)
        self.assertEqual(records[0]["prepare_error"], "OSError")
        self.assertEqual(records[0]["runs"], {})
        self.assertNotIn("private", json.dumps(records))

    def test_report_new_face_area_embeddings_latencies_and_top_twenty(self):
        records = [{"media_id": index, "had_faces": index % 2 == 0, "runs": {
            "640": run([face()], .1),
            "1280": run([face((2, 2, 12, 22)), face((40, 40, 56, 56), [1, 0])], .2),
        }} for index in range(1, 26)]
        text = experiment.report_markdown(records, ["CPU note."], dry_run=False)
        self.assertIn("| Faces found | 25 | 50 |", text)
        self.assertIn("Images gaining faces: 25. Images losing faces: 0", text)
        self.assertIn("Unmatched 1280 boxes: 25", text)
        self.assertIn("| < 32² | 25 |", text)
        self.assertIn("25/50 (50.0%)", text)
        self.assertIn("100.00 ms | 200.00 ms", text)
        top = text.split("## Largest gains: media ids for manual inspection\n\n")[1].split("\n\n")[0]
        self.assertEqual(top, ", ".join(map(str, range(1, 21))))
        self.assertIn("CPU note.", text)
        self.assertIn('"embedding_produced": true', text)

    def test_matching_is_one_to_one_and_counts_rediscovery_when_net_gain_is_zero(self):
        before = [experiment.face_record(face((0, 0, 20, 20)))]
        after = [experiment.face_record(face((0, 0, 20, 20))), experiment.face_record(face((1, 1, 21, 21)))]
        self.assertEqual(len(experiment.new_faces(before, after)), 1)
        self.assertEqual(len(experiment.new_faces(before, [experiment.face_record(face((100, 100, 120, 120)))])), 1)
        self.assertEqual(experiment.new_faces(before, before), [])
        self.assertAlmostEqual(experiment.percentile([1, 2, 3, 4, 5], .95), 4.8)

    def test_unit_launch_preserves_python_models_threshold_but_replaces_socket_device_size(self):
        unit = self.root / "service.unit"
        unit.write_text('[Service]\nEnvironment=HIP_VISIBLE_DEVICES=0\nEnvironment="LD_LIBRARY_PATH=/rocm/lib"\n'
                        'ExecStart=/unit/python -m beep_photo_face.service --socket /production.sock --model-root /m --detector /d --aligner /a --recognizer /r --device 2 --detection-threshold 0.6\n')
        command, env = experiment.read_unit(unit)
        launch = experiment.service_command(command, 1280, self.root / "experiment.sock")
        self.assertEqual(launch[0], "/unit/python")
        self.assertNotIn("/production.sock", launch)
        for flag, value in (("--det-size", "1280"), ("--device", "0"), ("--detection-threshold", "0.6"), ("--recognizer", "/r")):
            self.assertEqual(launch[launch.index(flag) + 1], value)
        with patch.dict("os.environ", {"ROCR_VISIBLE_DEVICES": "0", "CUDA_VISIBLE_DEVICES": "0"}):
            environment = experiment.service_environment(env, self.root, 1)
        self.assertEqual(environment["HIP_VISIBLE_DEVICES"], "1")
        self.assertNotIn("ROCR_VISIBLE_DEVICES", environment)
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/rocm/lib")
        self.assertEqual(environment["PYTHONPATH"], str(experiment.REPO / "vendor/photo-face"))

    def test_gpu_one_selection_and_explicit_zero_fallback(self):
        with patch.object(experiment.subprocess, "run", return_value=SimpleNamespace(stdout='{"gpu1": true}')):
            self.assertEqual(experiment.choose_gpu("python", {})[0], 1)
        with patch.object(experiment.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 60)):
            gpu, note = experiment.choose_gpu("python", {})
            self.assertEqual(gpu, 0)
            self.assertIn("falling back to GPU 0", note)

    def test_owned_services_are_stopped_on_partial_startup_failure(self):
        process = MagicMock()
        process.poll.return_value = None
        command = ["python", "-m", "beep_photo_face.service", "--model-root", "/m", "--detector", "/d", "--aligner", "/a", "--recognizer", "/r"]
        with patch.object(experiment, "read_unit", return_value=(command, {})), \
             patch.object(experiment, "choose_gpu", return_value=(1, "GPU 1")), \
             patch.object(experiment, "wait_ready", side_effect=RuntimeError("failed")), \
             patch.object(experiment.subprocess, "Popen", return_value=process) as popen, redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                with experiment.managed_services(self.root / "unit", self.root, 1):
                    self.fail("must not become ready")
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=30)
        self.assertEqual(popen.call_args.kwargs["env"]["HIP_VISIBLE_DEVICES"], "1")

    def test_health_refuses_wrong_size(self):
        client = SimpleNamespace(health=lambda: {"detSize": 640})
        with self.assertRaisesRegex(experiment.ExperimentError, "wrong detSize"):
            experiment.wait_ready(client, 1280, timeout=1)

    def test_dry_run_exercises_five_hundred_images_without_db_sources_or_subprocesses(self):
        out = self.root / "report.md"
        args = experiment.build_parser().parse_args(["--dry-run", "--out", str(out)])
        with patch.object(experiment, "readonly_database", side_effect=AssertionError("library opened")), \
             patch.object(Image, "open", side_effect=AssertionError("source opened")), \
             patch.object(experiment.subprocess, "Popen", side_effect=AssertionError("service started")), \
             patch.object(experiment.subprocess, "run", side_effect=AssertionError("GPU probe")), \
             redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(experiment.execute(args), 0)
        text = out.read_text()
        self.assertIn("DRY RUN", text)
        self.assertIn("Complete pairs: 500", text)
        self.assertIn("250 with existing faces, 250 with zero existing faces", text)
        records = json.loads(text.split("```json\n")[1].split("\n```")[0])
        self.assertEqual(len(records), 500)
        self.assertTrue(all(row["working_size"] == [1280, 640] for row in records))
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.assertEqual(list(self.root.iterdir()), [out])
        with self.assertRaisesRegex(experiment.ExperimentError, "Output already exists"):
            experiment.execute(args)

    def test_output_refuses_symlink_to_existing_database(self):
        database = self.database()
        out = self.root / "report.md"
        out.symlink_to(database)
        args = experiment.build_parser().parse_args(["--dry-run", "--out", str(out)])
        with self.assertRaises(experiment.ExperimentError):
            experiment.execute(args)


if __name__ == "__main__":
    unittest.main()
