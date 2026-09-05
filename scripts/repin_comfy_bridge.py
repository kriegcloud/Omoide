"""Re-pin the ComfyUI bridge's artifact digests after a ComfyUI update.

The bridge refuses to serve when any pinned executor artifact (ComfyUI core
files, the git revision, the Omoide custom nodes) or model artifact drifts from
the digests recorded in ``integrations/comfyui/bridge/config.py``. This tool
compares those pins against the files on disk and, with ``--write``, rewrites
the constants in place so a deliberate ComfyUI upgrade is a one-command re-pin.

Usage::

    .venv/bin/python scripts/repin_comfy_bridge.py            # report drift, exit 1 if any
    .venv/bin/python scripts/repin_comfy_bridge.py --write    # rewrite config.py pins
    .venv/bin/python scripts/repin_comfy_bridge.py --skip-models   # executor artifacts only

After ``--write`` run ``packaging/omoide-comfy-bridge-launcher --check`` and
``systemctl --user restart omoide-comfy-bridge.service``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "integrations" / "comfyui" / "bridge" / "config.py"
COMFY_ROOT = Path("/home/elpresidank/ai/ComfyUI")


@dataclass
class Replacement:
    node: ast.Constant
    value: str | int


@dataclass
class Drift:
    name: str
    field: str
    pinned: str
    actual: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _constant(node: ast.expr | None) -> ast.Constant | None:
    return node if isinstance(node, ast.Constant) else None


def _path_literal(node: ast.expr | None) -> Path | None:
    # path=Path("..." "...") — adjacent literals fold into one Constant.
    if isinstance(node, ast.Call) and node.args:
        constant = _constant(node.args[0])
        if constant is not None and isinstance(constant.value, str):
            return Path(constant.value)
    return None


def _model_artifact_spans(tree: ast.Module) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ProfileBinding":
            artifacts = _keyword(node, "artifacts")
            if artifacts is not None and artifacts.end_lineno is not None:
                spans.append((artifacts.lineno, artifacts.end_lineno))
    return spans


def _comfy_head() -> tuple[str, str]:
    revision = subprocess.run(
        ["git", "-C", str(COMFY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    namespace: dict[str, str] = {}
    exec((COMFY_ROOT / "comfyui_version.py").read_text(encoding="utf-8"), namespace)
    return revision, str(namespace["__version__"])


def _render(node: ast.Constant, value: str | int, source_lines: list[str]) -> str:
    if isinstance(value, int):
        return f"{value:_d}"
    if node.end_lineno is not None and node.end_lineno > node.lineno and len(value) == 64:
        # Keep the wrapped two-half layout used for long digests.
        indent = " " * node.col_offset
        return f'"{value[:32]}"\n{indent}"{value[32:]}"'
    return f'"{value}"'


def _apply(source: str, replacements: list[Replacement]) -> str:
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def absolute(lineno: int, col: int) -> int:
        # ast columns are UTF-8 byte offsets; config.py is ASCII.
        return offsets[lineno - 1] + col

    ordered = sorted(
        replacements,
        key=lambda item: absolute(item.node.lineno, item.node.col_offset),
        reverse=True,
    )
    text = source
    for item in ordered:
        node = item.node
        assert node.end_lineno is not None and node.end_col_offset is not None
        start = absolute(node.lineno, node.col_offset)
        end = absolute(node.end_lineno, node.end_col_offset)
        text = text[:start] + _render(node, item.value, lines) + text[end:]
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="rewrite config.py pins")
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="do not hash model artifacts (multi-GB files); executor pins only",
    )
    args = parser.parse_args()

    source = CONFIG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    model_spans = _model_artifact_spans(tree)
    drifts: list[Drift] = []
    replacements: list[Replacement] = []
    revision, comfy_version = _comfy_head()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None)
        if func_name == "ProfileBinding":
            for field, expected in (
                ("comfy_core_commit", revision),
                ("comfy_version", comfy_version),
            ):
                constant = _constant(_keyword(node, field))
                if constant is None:
                    continue
                if constant.value != expected:
                    drifts.append(Drift("ProfileBinding", field, str(constant.value), expected))
                    replacements.append(Replacement(constant, expected))
            continue
        if func_name != "ArtifactBinding":
            continue
        is_model = any(start <= node.lineno <= end for start, end in model_spans)
        if is_model and args.skip_models:
            continue
        path = _path_literal(_keyword(node, "path"))
        name_node = _constant(_keyword(node, "name"))
        size_node = _constant(_keyword(node, "bytes_expected"))
        sha_node = _constant(_keyword(node, "sha256"))
        if path is None or size_node is None or sha_node is None:
            print(f"skipping unparseable ArtifactBinding at line {node.lineno}", file=sys.stderr)
            continue
        label = str(name_node.value) if name_node is not None else path.name
        if not path.is_file():
            drifts.append(Drift(label, "path", str(path), "MISSING"))
            continue
        actual_size = path.stat().st_size
        actual_sha = _sha256(path)
        if size_node.value != actual_size:
            drifts.append(Drift(label, "bytes_expected", str(size_node.value), str(actual_size)))
            replacements.append(Replacement(size_node, actual_size))
        if sha_node.value != actual_sha:
            drifts.append(Drift(label, "sha256", str(sha_node.value), actual_sha))
            replacements.append(Replacement(sha_node, actual_sha))
        if (
            name_node is not None
            and isinstance(name_node.value, str)
            and name_node.value.startswith("ComfyUI git revision ")
        ):
            expected_name = f"ComfyUI git revision {revision[:8]}"
            if name_node.value != expected_name:
                drifts.append(Drift(label, "name", name_node.value, expected_name))
                replacements.append(Replacement(name_node, expected_name))

    if not drifts:
        print(f"bridge pins match disk (ComfyUI {comfy_version} @ {revision[:8]})")
        return 0

    width = max(len(d.name) for d in drifts)
    for drift in drifts:
        print(f"{drift.name:<{width}}  {drift.field:<16} {drift.pinned}  ->  {drift.actual}")
    missing = [d for d in drifts if d.actual == "MISSING"]
    if missing:
        print("\nmissing artifacts cannot be re-pinned; restore them first", file=sys.stderr)
        return 2
    if not args.write:
        print(f"\n{len(drifts)} drift(s); rerun with --write to re-pin", file=sys.stderr)
        return 1
    CONFIG_PATH.write_text(_apply(source, replacements), encoding="utf-8")
    ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    print(
        f"\nre-pinned {len(replacements)} value(s) in {CONFIG_PATH.relative_to(REPO_ROOT)}; "
        "now run packaging/omoide-comfy-bridge-launcher --check and restart "
        "omoide-comfy-bridge.service"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
