import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

VENDOR = Path(__file__).resolve().parents[1] / "vendor/photo-face"
sys.path.insert(0, str(VENDOR))
from beep_photo_face import service
from beep_photo_face.backends import adaface_kprpe as backend
from beep_photo_face.backends.base import WorkerError


class DetectorSizeServiceTests(unittest.TestCase):
    def arguments(self, *extra):
        return service.build_parser().parse_args([
            "--socket", "/tmp/test.sock", "--model-root", "/models", "--detector", "/models/det",
            "--aligner", "/models/aligner", "--recognizer", "/models/recognizer", *extra,
        ])

    def test_default_and_valid_flag_sizes(self):
        self.assertEqual(self.arguments().det_size, 640)
        for size in (320, 640, 1280, 1600):
            with self.subTest(size=size):
                self.assertEqual(self.arguments("--det-size", str(size)).det_size, size)

    def test_rejects_non_multiple_out_of_range_and_non_integer(self):
        for value in ("1000", "288", "1632", "640.5", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.arguments("--det-size", value)
                self.assertEqual(raised.exception.code, 2)

    def test_service_passes_square_size_and_reports_health(self):
        for size in (640, 1280):
            with self.subTest(size=size), patch.object(service, "load_backend") as loader:
                loader.return_value.model = {"runtime": {"actualCompute": "rocm"}}
                instance = service.AdaFaceService(self.arguments("--det-size", str(size)))
                self.assertEqual(loader.call_args.kwargs["det_size"], (size, size))
                self.assertEqual(loader.call_args.args[0].detection_threshold, .5)
                health = instance.handle({"protocol": service.PROTOCOL_VERSION, "action": "health"})
                self.assertEqual(health["detSize"], size)
                self.assertEqual(health["runtime"]["actualCompute"], "rocm")

    def test_backend_prepares_requested_size_and_keeps_cpu_provider_assertion(self):
        # Stop at the provider assertion after observing the real prepare call;
        # no GPU, model artifact or network download is needed.
        args = SimpleNamespace(detection_threshold=.5)
        for size in ((640, 640), (1280, 1280)):
            detector = MagicMock()
            detector.session.get_providers.return_value = ["CUDAExecutionProvider"]
            zoo = SimpleNamespace(get_model=MagicMock(return_value=detector))
            selection = SimpleNamespace(actual_compute="cpu", device_ordinals=())
            with self.subTest(size=size), \
                 patch.object(backend, "_import_adaface_dependencies", return_value=(MagicMock(), MagicMock())), \
                 patch.object(backend, "resolve_compute", return_value=selection), \
                 patch.object(backend, "_resolve_artifact_paths", return_value=(Path("d"), Path("a"), Path("r"))), \
                 patch.object(backend.importlib, "import_module", return_value=zoo):
                with self.assertRaises(WorkerError) as raised:
                    backend.load_backend(args, None, det_size=size)
                self.assertEqual(raised.exception.code, "unexpected-execution-provider")
                zoo.get_model.assert_called_once_with("d", providers=["CPUExecutionProvider"])
                detector.prepare.assert_called_once_with(ctx_id=-1, input_size=size, det_thresh=.5)

    def test_backend_rejects_detector_that_silently_ignores_requested_size(self):
        detector = MagicMock(input_size=(640, 640))
        detector.session.get_providers.return_value = ["CPUExecutionProvider"]
        zoo = SimpleNamespace(get_model=MagicMock(return_value=detector))
        with patch.object(backend, "_import_adaface_dependencies", return_value=(MagicMock(), MagicMock())), \
             patch.object(backend, "resolve_compute", return_value=SimpleNamespace(actual_compute="cpu")), \
             patch.object(backend, "_resolve_artifact_paths", return_value=(Path("d"), Path("a"), Path("r"))), \
             patch.object(backend.importlib, "import_module", return_value=zoo), \
             patch.object(backend, "_build_aligner", side_effect=AssertionError("must reject before GPU model loading")):
            with self.assertRaises(WorkerError) as raised:
                backend.load_backend(SimpleNamespace(detection_threshold=.5), None, det_size=(1280, 1280))
            self.assertEqual(raised.exception.code, "unexpected-detector-size")


if __name__ == "__main__":
    unittest.main()
