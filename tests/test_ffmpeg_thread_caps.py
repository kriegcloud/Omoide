"""ffmpeg calls that grab single frames must not auto-thread.

Without ``-threads``, every input decoder and every mjpeg output encoder
spawns roughly one thread per core. On the 64-core workstation one batched
16-frame grab peaked at ~1,550 threads, so a few concurrent videos exhausted
the container's ``pids_limit`` (2048); a C++ thread spawn then aborted the
server process mid-task (2026-11-02). ``-threads 1`` peaks at 65 threads and
is faster for single frames.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import frames, utils
from app.ffmpeg import SINGLE_FRAME_THREAD_ARGS


def _assert_capped_before(testcase, cmd, positions):
    for pos in positions:
        testcase.assertEqual(
            tuple(cmd[pos - 2 : pos]),
            SINGLE_FRAME_THREAD_ARGS,
            f"expected {SINGLE_FRAME_THREAD_ARGS} before {cmd[pos]!r} in {cmd}",
        )


def _input_positions(cmd):
    # "-ss <t>" may sit between the cap and "-i"; anchor on the -ss when present.
    out = []
    for i, arg in enumerate(cmd):
        if arg == "-i":
            out.append(i - 2 if cmd[i - 2] == "-ss" else i)
    return out


class FfmpegThreadCapTests(unittest.TestCase):
    def test_single_frame_args_are_one_thread(self):
        self.assertEqual(SINGLE_FRAME_THREAD_ARGS, ("-threads", "1"))

    def test_batch_extraction_caps_every_input_and_output(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch.object(frames, "run_silent", side_effect=fake_run):
            frames._extract_frames_batch(
                Path("ffmpeg"), Path("/v.mp4"), [0, 1, 2], [0.0, 1.0, 2.0]
            )
        cmd = captured[0]
        _assert_capped_before(self, cmd, _input_positions(cmd))
        outputs = [i for i, a in enumerate(cmd) if a.endswith(".jpg")]
        self.assertEqual(len(outputs), 3)
        for pos in outputs:
            window = cmd[max(0, pos - 14) : pos]
            self.assertIn("-threads", window, f"output {cmd[pos]} not capped")
            self.assertEqual(window[window.index("-threads") + 1], "1")

    def test_fallback_worker_caps_input_and_output(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, b"", b"")

        with patch.object(frames, "run_silent", side_effect=fake_run):
            frames._extract_frame_worker(
                Path("ffmpeg"), Path("/v.mp4"), 1.0, 0, [1.0], 2.0, 1
            )
        cmd = captured[0]
        _assert_capped_before(self, cmd, _input_positions(cmd))
        self.assertGreaterEqual(cmd.count("-threads"), 2, cmd)

    def test_video_thumbnail_caps_threads(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            Path(cmd[-1]).write_bytes(b"x")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            media = SimpleNamespace(id=7, path=str(Path(tmp) / "clip.mp4"))
            accel = SimpleNamespace(hwaccel_args=[])
            with (
                patch.object(utils, "run_silent", side_effect=fake_run),
                patch.object(utils, "get_ffmpeg_accel_config", return_value=accel),
                patch.object(utils, "get_thumb_folder", return_value=Path(tmp)),
                patch.object(
                    type(utils.settings.general),
                    "thumb_dir",
                    property(lambda _self: Path(tmp)),
                ),
            ):
                utils.generate_thumbnail(media)
        cmd = captured[0]
        _assert_capped_before(self, cmd, _input_positions(cmd))
        self.assertGreaterEqual(cmd.count("-threads"), 2, cmd)


if __name__ == "__main__":
    unittest.main()
