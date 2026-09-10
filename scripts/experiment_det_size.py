#!/usr/bin/env python3
"""Compare SCRFD 640/1280 without mutating Omoide or its production service.

Run with Omoide's .venv Python. By default the script owns two temporary service
processes; their Python, model flags and ROCm library path come from the user
unit, while PYTHONPATH points at this checkout. --dry-run needs no library/GPU.
Only the Markdown report is persisted, including path-free per-image records.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import random
import shlex
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# These pure modules deliberately do not import config, models or app.database:
# importing the application settings can create a profile/log/database on host.
from app.services.face_inference import AdaFaceSocketAnalysis, PROTOCOL_VERSION
from app.services.face_working_image import face_scene_rgb, face_working_image

SIZES = (640, 1280)
SEED = 7
PER_STRATUM = 250
MATCH_IOU = 0.3


class ExperimentError(RuntimeError):
    """An intentionally path-free diagnostic safe for the report/terminal."""


@dataclass(frozen=True)
class Sample:
    media_id: int
    had_faces: bool
    path: str = field(repr=False)


@contextmanager
def readonly_database(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        # Pin one read snapshot for eligibility, strata and selected paths.
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def sample_media(connection: sqlite3.Connection, per_stratum: int = PER_STRATUM) -> list[Sample]:
    # duration IS NULL is Omoide's image-list predicate. Query paths only AFTER
    # selecting ids, and never check filesystem existence outside this sample.
    candidates = connection.execute(
        "SELECT m.id, EXISTS(SELECT 1 FROM face f WHERE f.media_id=m.id) "
        "FROM media m WHERE m.duration IS NULL AND m.faces_extracted=1 "
        "AND m.processing_error IS NULL ORDER BY m.id"
    ).fetchall()
    rng = random.Random(SEED)
    chosen: list[tuple[int, bool]] = []
    for had_faces in (True, False):
        pool = [int(row[0]) for row in candidates if bool(row[1]) == had_faces]
        if len(pool) < per_stratum:
            raise ExperimentError(
                f"Insufficient eligible images in {'with-face' if had_faces else 'zero-face'} "
                f"stratum: {len(pool)} available, {per_stratum} required."
            )
        chosen.extend((media_id, had_faces) for media_id in rng.sample(pool, per_stratum))
    rng.shuffle(chosen)
    return [
        Sample(media_id, had_faces, connection.execute(
            "SELECT path FROM media WHERE id=?", (media_id,)
        ).fetchone()[0])
        for media_id, had_faces in chosen
    ]


def parse_root_mapping(value: str) -> tuple[Path, Path]:
    left, separator, right = value.partition("=")
    if not separator or not Path(left).is_absolute() or not Path(right).is_absolute():
        raise argparse.ArgumentTypeError("media-root requires absolute DB_PREFIX=HOST_PREFIX")
    return Path(left), Path(right)


def source_path(sample: Sample, mappings: list[tuple[Path, Path]]) -> Path:
    path = Path(sample.path)
    for prefix, host in sorted(mappings, key=lambda item: len(item[0].parts), reverse=True):
        if path.is_relative_to(prefix):
            relative = path.relative_to(prefix)
            if ".." in relative.parts:
                raise ExperimentError("Sample path contains parent traversal.")
            return host / relative
    if path.is_relative_to(Path("/app/media")):
        raise ExperimentError("Sample requires a --media-root mapping.")
    return path


def prepare_sample(sample: Sample, mappings: list[tuple[Path, Path]]) -> np.ndarray:
    # Only a sampled source is opened. HEIF support matches app.utils, without
    # importing its database/config initialization or thumbnail write paths.
    import pillow_heif
    pillow_heif.register_heif_opener()
    with Image.open(source_path(sample, mappings)) as original:
        return face_working_image(face_scene_rgb(original))


def read_unit(path: Path) -> tuple[list[str], dict[str, str]]:
    """Read only ExecStart and needed nonsecret ROCm settings; no systemctl writes."""
    command: list[str] | None = None
    environment: dict[str, str] = {}
    section = ""
    contents = path.read_text().replace("\\\n", " ")
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line
        if section != "[Service]":
            continue
        if line.startswith("ExecStart="):
            command = shlex.split(line.partition("=")[2])
        if line.startswith("Environment="):
            for entry in shlex.split(line.partition("=")[2]):
                key, _, value = entry.partition("=")
                if key == "LD_LIBRARY_PATH":
                    environment[key] = value
    if not command or command[1:3] != ["-m", "beep_photo_face.service"]:
        raise ExperimentError("Unit does not contain the expected AdaFace Python command.")
    return command, environment


def service_command(unit_command: list[str], size: int, socket_path: Path) -> list[str]:
    # Copy only understood model/runtime options. Never copy the unit socket.
    flags = unit_command[3:]
    allowed = {"--model-root", "--detector", "--aligner", "--recognizer",
               "--batch-size", "--detection-threshold", "--timeout"}
    command = unit_command[:3]
    seen: set[str] = set()
    for index in range(0, len(flags), 2):
        flag = flags[index]
        if index + 1 >= len(flags) or flag not in allowed | {"--socket", "--device", "--det-size"}:
            raise ExperimentError("Unit has unsupported service arguments; review the launch configuration.")
        if flag in allowed:
            command.extend(flags[index:index + 2])
            seen.add(flag)
    if not {"--model-root", "--detector", "--aligner", "--recognizer"}.issubset(seen):
        raise ExperimentError("Unit is missing explicit pinned model paths.")
    return command + ["--device", "0", "--socket", str(socket_path), "--det-size", str(size)]


def service_environment(unit_environment: dict[str, str], scratch: Path, gpu: int) -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL"):
        environment.pop(name, None)
    environment.update(unit_environment)
    environment.update(
        PYTHONPATH=str(REPO / "vendor/photo-face"), HIP_VISIBLE_DEVICES=str(gpu),
        PYTHONDONTWRITEBYTECODE="1", XDG_CACHE_HOME=str(scratch / "cache"),
        XDG_CONFIG_HOME=str(scratch / "config"),
        MIOPEN_USER_DB_PATH=str(scratch / "miopen-db"),
        MIOPEN_CUSTOM_CACHE_DIR=str(scratch / "miopen-cache"),
    )
    return environment


def choose_gpu(python: str, environment: dict[str, str]) -> tuple[int, str]:
    probe_environment = dict(environment)
    probe_environment.pop("HIP_VISIBLE_DEVICES", None)
    # Test ROCm visibility and a small allocation on physical GPU 1 using the
    # same interpreter/library path as the unit. Do not touch production IPC.
    probe = (
        "import json, torch\n"
        "available = False\n"
        "try:\n"
        " if torch.version.hip and torch.cuda.device_count() > 1:\n"
        "  x = torch.zeros(1, device='cuda:1'); torch.cuda.synchronize(1); available = True\n"
        "except Exception:\n"
        " pass\n"
        "print(json.dumps({'gpu1': available}))\n"
    )
    try:
        result = subprocess.run([python, "-c", probe], env=probe_environment,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=60, check=True)
        if json.loads(result.stdout).get("gpu1") is True:
            return 1, "GPU 1 available; HIP_VISIBLE_DEVICES=1, service logical device 0."
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 0, "GPU 1 unavailable or probe failed; falling back to GPU 0 (may contend with production)."


def wait_ready(client: AdaFaceSocketAnalysis, size: int, process=None, timeout: float = 300) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise ExperimentError(f"Experiment service {size} exited before becoming ready.")
        try:
            health = client.health()
        except Exception:
            time.sleep(0.2)
            continue
        if health.get("detSize") != size:
            raise ExperimentError(f"Experiment service {size} reports the wrong detSize; use this checkout's service.")
        return health
    raise ExperimentError(f"Experiment service {size} did not become ready before timeout.")


@contextmanager
def managed_services(unit: Path, scratch: Path, startup_timeout: float):
    unit_command, unit_environment = read_unit(unit)
    environment = service_environment(unit_environment, scratch, 1)
    gpu, note = choose_gpu(unit_command[0], environment)
    environment["HIP_VISIBLE_DEVICES"] = str(gpu)
    print(note, flush=True)
    processes: dict[int, Any] = {}
    clients: dict[int, AdaFaceSocketAnalysis] = {}
    try:
        for size in SIZES:
            child_dir = scratch / str(size)
            child_dir.mkdir()
            child_environment = service_environment(unit_environment, child_dir, gpu)
            socket_path = scratch / f"det-{size}.sock"
            processes[size] = subprocess.Popen(
                service_command(unit_command, size, socket_path), env=child_environment,
                cwd=REPO / "vendor/photo-face", stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            ready_client = AdaFaceSocketAnalysis(socket_path, timeout_seconds=1)
            wait_ready(ready_client, size, processes[size], startup_timeout)
            clients[size] = AdaFaceSocketAnalysis(socket_path)
        yield clients, processes, note
    finally:
        # Only the exact children this script created are signalled.
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


class Utilisation:
    """Best-effort Linux host snapshots; no external monitor or media access."""
    def __init__(self, processes: dict[int, Any]):
        self.processes = processes
        self.cpu_before: dict[int, float] = {}
        self.gpu: dict[str, list[int]] = {}
        self.load_before = os.getloadavg()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    @staticmethod
    def cpu_seconds(pid: int) -> float:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")

    def poll_gpu(self):
        while not self.stop.is_set():
            for path in Path("/sys/class/drm").glob("card[0-9]*/device/gpu_busy_percent"):
                try:
                    label = path.parents[1].name
                    self.gpu.setdefault(label, []).append(int(path.read_text()))
                except (OSError, ValueError):
                    pass
            self.stop.wait(0.5)

    def __enter__(self):
        self.started = time.perf_counter()
        for size, process in self.processes.items():
            try:
                self.cpu_before[size] = self.cpu_seconds(process.pid)
            except (OSError, ValueError):
                pass
        self.thread = threading.Thread(target=self.poll_gpu, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.elapsed = time.perf_counter() - self.started
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def notes(self) -> list[str]:
        notes = [f"Host load average (1/5/15 min) at start: {', '.join(f'{v:.2f}' for v in self.load_before)}."]
        for size, before in self.cpu_before.items():
            try:
                used = self.cpu_seconds(self.processes[size].pid) - before
                notes.append(f"Service {size}: {used:.2f} CPU seconds over {self.elapsed:.2f} s "
                             f"paired sweep, {100 * used / max(self.elapsed, 1e-9):.1f}% of one CPU core on average.")
            except (OSError, ValueError):
                notes.append(f"Service {size}: CPU measurement unavailable.")
        if not self.cpu_before:
            notes.append("Per-process CPU utilisation unavailable (external services or inaccessible procfs).")
        for label, values in sorted(self.gpu.items()):
            if values:
                notes.append(f"{label}: GPU busy mean {statistics.mean(values):.1f}%, "
                             f"p95 {percentile(values, .95):.1f}%, {len(values)} samples at ~0.5 s.")
        if not any(self.gpu.values()):
            notes.append("GPU busy counters unavailable; GPU utilisation was not measured.")
        notes.append("GPU counters cover whole DRM devices, include other workloads, and are not mapped to HIP ordinals. "
                     "CPU averages include the other service's turn; they are not isolated saturation measurements.")
        return notes


def face_record(face: Any) -> dict:
    bbox = [float(value) for value in np.asarray(face.bbox).ravel()[:4]]
    score = float(face.det_score)
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox + [score]):
        raise ExperimentError("Service returned invalid face geometry or score.")
    embedding = getattr(face, "embedding", None)
    return {"bbox": bbox, "area": max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1]),
            "det_score": score, "embedding_produced": embedding is not None}


def run_images(samples: list[Sample], clients: dict[int, Any], prepare, *, warmup: bool = True) -> list[dict]:
    records = []
    warmed: set[int] = set() if warmup else set(SIZES)
    for index, sample in enumerate(samples):
        record: dict[str, Any] = {"media_id": sample.media_id, "had_faces": sample.had_faces, "runs": {}}
        try:
            image = prepare(sample)
            record["working_size"] = [int(image.shape[1]), int(image.shape[0])]
        except Exception as error:
            # Exception messages can contain personal source paths; retain only
            # the class. Failure is not a zero-face detection.
            record["prepare_error"] = type(error).__name__
            records.append(record)
            continue
        order = SIZES if index % 2 == 0 else tuple(reversed(SIZES))
        for size in order:
            if size not in warmed:
                try:
                    clients[size].get(image)
                except Exception:
                    raise ExperimentError(f"Service {size} warm-up failed.") from None
                warmed.add(size)
            start = time.perf_counter()
            try:
                faces = clients[size].get(image)
                elapsed = time.perf_counter() - start
                details = [face_record(face) for face in faces]
                record["runs"][str(size)] = {"faces_found": len(details), "faces": details, "seconds": elapsed}
            except Exception as error:
                record["runs"][str(size)] = {"error": type(error).__name__, "seconds": time.perf_counter() - start}
        records.append(record)
    return records


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (ordered[math.ceil(position)] - ordered[lower]) * (position - lower)


def box_iou(a: list[float], b: list[float]) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = max(0, a[2] - a[0]) * max(0, a[3] - a[1]) + max(0, b[2] - b[0]) * max(0, b[3] - b[1]) - intersection
    return intersection / union if union else 0.0


def new_faces(before: list[dict], after: list[dict]) -> list[dict]:
    # Maximum-cardinality one-to-one matching prevents a greedy early match
    # making an overlapping, already-detected face appear "new". Highest IoU
    # edges are tried first for stable tie handling. This is geometric, not an
    # identity assertion; a shifted box or false positive can remain unmatched.
    neighbours = [[index for _, index in sorted(
        ((box_iou(face["bbox"], old["bbox"]), index) for index, old in enumerate(before)),
        reverse=True,
    ) if box_iou(face["bbox"], before[index]["bbox"]) >= MATCH_IOU] for face in after]
    matched: dict[int, int] = {}

    def assign(index: int, visited: set[int]) -> bool:
        for old_index in neighbours[index]:
            if old_index in visited:
                continue
            visited.add(old_index)
            if old_index not in matched or assign(matched[old_index], visited):
                matched[old_index] = index
                return True
        return False

    for index in range(len(after)):
        assign(index, set())
    matched_new = set(matched.values())
    return [face for index, face in enumerate(after) if index not in matched_new]


def report_markdown(records: list[dict], notes: list[str], *, dry_run: bool) -> str:
    paired = [row for row in records if all(
        str(size) in row["runs"] and "error" not in row["runs"][str(size)] for size in SIZES
    )]
    gains = [(row["runs"]["1280"]["faces_found"] - row["runs"]["640"]["faces_found"], row["media_id"]) for row in paired]
    added = [face for row in paired for face in new_faces(row["runs"]["640"]["faces"], row["runs"]["1280"]["faces"])]
    areas = [face["area"] for face in added]
    lines = ["# SCRFD detector resolution experiment", "",
             "**DRY RUN — synthetic images and detections; these are not library findings.**" if dry_run else "Read-only library experiment; no annotations or face assignments were written.", "",
             f"Seed: {SEED}. Sample: {len(records)} images; {sum(row['had_faces'] for row in records)} with existing faces, "
             f"{sum(not row['had_faces'] for row in records)} with zero existing faces.",
             f"Complete pairs: {len(paired)}. Excluded incomplete pairs: {len(records) - len(paired)}. "
             "All face totals and latency comparisons below use these same complete pairs.", "",
             "| Metric | 640 | 1280 |", "| --- | ---: | ---: |"]
    metrics: dict[int, dict] = {}
    for size in SIZES:
        runs = [row["runs"][str(size)] for row in paired]
        faces = [face for run in runs for face in run["faces"]]
        times = [run["seconds"] for run in runs]
        embeddings = sum(face["embedding_produced"] for face in faces)
        metrics[size] = {"faces": len(faces), "embeddings": f"{embeddings}/{len(faces)} ({100 * embeddings / len(faces):.1f}%)" if faces else "0/0 (n/a)",
                         "median": f"{statistics.median(times) * 1000:.2f} ms" if times else "n/a",
                         "p95": f"{percentile(times, .95) * 1000:.2f} ms" if times else "n/a",
                         "cap": sum(run["faces_found"] == 32 for run in runs)}
    for label, key in (("Faces found", "faces"), ("Embeddings produced / detections", "embeddings"),
                       ("Median latency per image", "median"), ("p95 latency per image", "p95"), ("Images hitting 32-face cap", "cap")):
        lines.append(f"| {label} | {metrics[640][key]} | {metrics[1280][key]} |")
    lines += ["", f"Images gaining faces: {sum(gain > 0 for gain, _ in gains)}. "
              f"Images losing faces: {sum(gain < 0 for gain, _ in gains)}. "
              f"Unchanged counts: {sum(gain == 0 for gain, _ in gains)}.", "",
              "| Existing-face stratum | Paired images | Faces at 640 | Faces at 1280 | Images gaining |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for had_faces in (True, False):
        rows = [row for row in paired if row["had_faces"] == had_faces]
        totals = [sum(row["runs"][str(size)]["faces_found"] for row in rows) for size in SIZES]
        gaining = sum(row["runs"]["1280"]["faces_found"] > row["runs"]["640"]["faces_found"] for row in rows)
        lines.append(f"| {'At least one' if had_faces else 'Zero'} | {len(rows)} | {totals[0]} | {totals[1]} | {gaining} |")
    lines += ["", "## Newly found face area", "",
              f"Unmatched 1280 boxes: {len(areas)}. Maximum-cardinality one-to-one box matching at IoU >= {MATCH_IOU}; "
              "unmatched boxes are candidate new detections, not verified recall. Coordinates and areas use the working image (long side at most 1280), in pixels and pixels²."]
    if areas:
        lines += [f"Area min / median / p95 / max: {min(areas):.1f} / {statistics.median(areas):.1f} / {percentile(areas, .95):.1f} / {max(areas):.1f} pixels².", "",
                  "| Area (pixels²) | Candidate faces |", "| --- | ---: |"]
        for label, lower, upper in (("< 32²", 0, 1024), ("32²–<64²", 1024, 4096), ("64²–<128²", 4096, 16384), (">=128²", 16384, math.inf)):
            lines.append(f"| {label} | {sum(lower <= area < upper for area in areas)} |")
        lines.append(f"{100 * sum(area < 4096 for area in areas) / len(areas):.1f}% of candidate new faces have area below 64² working-image pixels² (area-based small-face proxy).")
    else:
        lines.append("No unmatched detections in complete pairs; area distribution is n/a.")
    top = [media_id for gain, media_id in sorted(gains, key=lambda item: (-item[0], item[1])) if gain > 0][:20]
    lines += ["", "## Largest gains: media ids for manual inspection", "", ", ".join(map(str, top)) or "None.", "",
              "## Measurement notes", "",
              "The same prepared RGB array is sent through AdaFaceSocketAnalysis (JPEG quality 95) to each service. "
              "EXIF transpose, RGB conversion and 1280-long-side preparation reuse the face pipeline helpers. "
              "Service order alternates per image. Each service gets one warm-up request before its measured requests. "
              "Timing covers client encoding, IPC, detector, alignment, recognition and response decoding; it excludes source decoding, resizing and service startup. "
              "Additional lazy kernel work may still affect early samples. SCRFD stays on CPU; aligner/recognizer use ROCm. "
              "Both runs retain the service's 32-face cap and the unit's detection threshold. "
              "This compares direct service detections; the application's padded retry and later face filtering are not applied. "
              "Existing face count is a sampling stratum, not ground truth. The deliberately balanced sample is not a library-wide prevalence estimate.", ""]
    lines.extend(f"- {note}" for note in notes)
    lines += ["", "## Per-image measurements", "", "Path-free records; embeddings are represented only by a produced/not-produced boolean. Failed calls are explicit errors, not zero detections.", "", "```json", json.dumps(records, indent=2, allow_nan=False), "```", ""]
    return "\n".join(lines)


class FakeClient(AdaFaceSocketAnalysis):
    """Use the real client encoding/parsing with an in-memory fake wire response."""
    def __init__(self, size: int):
        super().__init__(Path("/unused-dry-run.sock"))
        self.size = size

    def _request(self, payload: dict) -> dict:
        if payload["action"] == "health":
            return {"ok": True, "protocol": PROTOCOL_VERSION, "detSize": self.size}
        image = cv2.imdecode(np.frombuffer(base64.b64decode(payload["image"]), np.uint8), cv2.IMREAD_COLOR)
        marker = int(image[0, 0, 0])
        count = marker % 3 + (1 if self.size == 1280 and marker % 2 else 0)
        faces = [{"bbox": [10 + index * 80, 10, 30 + index * 80, 30], "detScore": .85,
                  "keypoints": [[12, 12]] * 5, "embedding": [1.0, 0.0] if index % 2 == 0 else None}
                 for index in range(count)]
        return {"ok": True, "protocol": PROTOCOL_VERSION, "faces": faces}


def synthetic_sample() -> list[Sample]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript("CREATE TABLE media(id INTEGER, path TEXT, duration REAL, faces_extracted INTEGER, processing_error TEXT); CREATE TABLE face(media_id INTEGER);")
        connection.executemany("INSERT INTO media VALUES (?, ?, NULL, 1, NULL)", ((index, f"synthetic-{index}") for index in range(1, 601)))
        connection.executemany("INSERT INTO face VALUES (?)", ((index,) for index in range(1, 301)))
        return sample_media(connection)


def synthetic_image(sample: Sample) -> np.ndarray:
    image = Image.new("RGB", (1600, 800), (sample.media_id % 251,) * 3)
    try:
        return face_working_image(face_scene_rgb(image))
    finally:
        image.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New Markdown file; existing files are never overwritten")
    parser.add_argument("--dry-run", action="store_true", help="500 synthetic samples, fake client, no library or GPU access")
    parser.add_argument("--db", type=Path, help="Library database, always opened mode=ro")
    parser.add_argument("--unit", type=Path, default=Path.home() / ".config/systemd/user/omoide-ml.service")
    parser.add_argument("--media-root", type=parse_root_mapping, action="append", default=[])
    parser.add_argument("--startup-timeout", type=float, default=300)
    parser.add_argument("--socket-640", type=Path, help="Optional externally started experiment socket under /tmp")
    parser.add_argument("--socket-1280", type=Path, help="Optional externally started experiment socket under /tmp")
    return parser


def execute(arguments: argparse.Namespace) -> int:
    if arguments.out.exists() or arguments.out.is_symlink():
        raise ExperimentError("Output already exists; choose a new report file.")
    if not arguments.out.parent.is_dir():
        raise ExperimentError("Output parent directory does not exist.")
    if arguments.dry_run:
        records = run_images(synthetic_sample(), {size: FakeClient(size) for size in SIZES}, synthetic_image)
        notes = ["Synthetic run: no GPU/CPU workload measurements; service startup, model loading and real inference require host verification."]
    else:
        if arguments.db is None:
            raise ExperimentError("--db is required unless --dry-run is used.")
        with readonly_database(arguments.db) as connection:
            samples = sample_media(connection)
        for sample in samples:
            source_path(sample, arguments.media_root)
        prepare = lambda sample: prepare_sample(sample, arguments.media_root)
        external = [arguments.socket_640, arguments.socket_1280]
        if any(external):
            if not all(external) or external[0] == external[1] or any(
                not path.resolve().is_relative_to(Path("/tmp")) for path in external
            ):
                raise ExperimentError("Both distinct external experiment sockets must be under /tmp.")
            clients = {size: AdaFaceSocketAnalysis(path) for size, path in zip(SIZES, external)}
            for size, client in clients.items():
                wait_ready(AdaFaceSocketAnalysis(client.socket_path, 1), size, timeout=arguments.startup_timeout)
            with Utilisation({}) as monitor:
                records = run_images(samples, clients, prepare)
            notes = ["External experiment services: GPU placement is controlled by their launch commands; this script did not start or stop them."] + monitor.notes()
        else:
            with tempfile.TemporaryDirectory(prefix="omoide-det-size-", dir="/tmp") as directory:
                with managed_services(arguments.unit, Path(directory), arguments.startup_timeout) as (clients, processes, note):
                    with Utilisation(processes) as monitor:
                        records = run_images(samples, clients, prepare)
                    notes = [note] + monitor.notes()
    # Exclusive creation prevents accidental overwrite of the DB, a source,
    # existing report, or symlink, even if the pathname changed during the run.
    with arguments.out.open("x", encoding="utf-8") as output:
        output.write(report_markdown(records, notes, dry_run=arguments.dry_run))
    incomplete = sum("prepare_error" in row or any("error" in run for run in row["runs"].values()) for row in records)
    print(f"Report written. Images: {len(records)}; incomplete pairs: {incomplete}. No library writes.", flush=True)
    return 2 if incomplete else 0


def main() -> int:
    arguments = build_parser().parse_args()
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return execute(arguments)
    except ExperimentError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Experiment interrupted; owned service processes have been stopped.", file=sys.stderr)
        return 130
    except Exception as error:
        # Never expose raw decoder, SQLite, subprocess or filesystem errors.
        print(f"Experiment failed ({type(error).__name__}); no source paths logged.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
