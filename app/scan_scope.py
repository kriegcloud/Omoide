"""Which folders under the media roots a library scan walks.

The scope is three lists from ``ScanSettings``:

* ``include_folders`` - folders (relative to each media root) to scan. Empty
  means the whole root.
* ``exclude_folders`` - folders never scanned, with everything below them.
  An exclusion always wins over an inclusion.
* ``exclude_folder_patterns`` - folder-name globs (``fnmatch``, case
  insensitive) skipped at any depth, e.g. ``@eaDir`` or ``*.photoslibrary``.

The scanner walk and the settings folder picker (listing and search) both
decide through
``ScanScope.is_excluded`` / ``folder_status`` so the preview in the UI is
the same rule the scan applies. Narrowing the scope never removes indexed media;
it only stops new files from being discovered there.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable, Literal

from app.logger import logger

FolderStatus = Literal["scanned", "partial", "outside", "excluded"]

# Folders that are never media: app state, OS trash and volume metadata.
INTERNAL_SKIPPED_DIR_NAMES = frozenset(
    {".omoide", ".Trash", "$RECYCLE.BIN", "System Volume Information"}
)


def is_internal_skipped_dir(name: str) -> bool:
    return name in INTERNAL_SKIPPED_DIR_NAMES or name.startswith(".Trash-")


def _split(value: str) -> list[str]:
    return [part for part in value.replace("\\", "/").split("/") if part]


def normalize_scope_folder(value: str) -> str:
    """Return ``value`` as a relative POSIX path, or raise ``ValueError``."""
    parts = _split(str(value).strip())
    if not parts:
        raise ValueError("Folder path must not be empty.")
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"Folder path must not contain '.' or '..': {value!r}")
    return "/".join(parts)


def normalize_scope_pattern(value: str) -> str:
    """Return a trimmed folder-name glob, or raise ``ValueError``."""
    pattern = str(value).strip()
    if not pattern or pattern in {".", ".."}:
        raise ValueError("Folder name pattern must not be empty.")
    if "/" in pattern or "\\" in pattern:
        raise ValueError(
            f"Folder name pattern matches one folder name, not a path: {value!r}"
        )
    return pattern


def normalize_scope_list(values: Iterable[str] | None, *, patterns: bool) -> list[str]:
    """Normalize and de-duplicate, keeping first-seen order."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.split(",") if v.strip()]
    normalize = normalize_scope_pattern if patterns else normalize_scope_folder
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = normalize(raw)
        key = item.casefold() if patterns else item
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


@dataclass(frozen=True)
class ScanScope:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, scan_settings) -> "ScanScope":
        return cls(
            include=tuple(getattr(scan_settings, "include_folders", None) or ()),
            exclude=tuple(getattr(scan_settings, "exclude_folders", None) or ()),
            patterns=tuple(
                getattr(scan_settings, "exclude_folder_patterns", None) or ()
            ),
        )

    @property
    def _include_parts(self) -> list[tuple[str, ...]]:
        return [tuple(_split(p)) for p in self.include]

    @property
    def _exclude_parts(self) -> list[tuple[str, ...]]:
        return [tuple(_split(p)) for p in self.exclude]

    def name_is_excluded(self, name: str) -> bool:
        if is_internal_skipped_dir(name):
            return True
        folded = name.casefold()
        return any(fnmatchcase(folded, p.casefold()) for p in self.patterns)

    def is_excluded(self, parts: tuple[str, ...]) -> bool:
        """True when the folder at ``parts`` (relative to a root) is skipped."""
        if any(self.name_is_excluded(part) for part in parts):
            return True
        return any(parts[: len(ex)] == ex for ex in self._exclude_parts)

    def start_folders(self) -> list[tuple[str, ...]]:
        """Relative folders to walk from each root; ``[()]`` is the root itself."""
        if not self.include:
            return [()]
        kept: list[tuple[str, ...]] = []
        for parts in sorted(set(self._include_parts), key=len):
            if self.is_excluded(parts):
                continue
            if any(parts[: len(k)] == k for k in kept):
                continue
            kept.append(parts)
        return kept


def folder_status(rel_path: str, scope: ScanScope) -> FolderStatus:
    """How a scan treats the folder at ``rel_path`` (relative to a root).

    ``scanned``  - walked (descendants may still be excluded).
    ``partial``  - not walked itself, but contains an included folder.
    ``outside``  - not walked: includes are set and none covers it.
    ``excluded`` - pruned by an exclude folder or a name pattern.
    """
    parts = tuple(_split(rel_path))
    if scope.is_excluded(parts):
        return "excluded"
    includes = scope._include_parts
    if not includes:
        return "scanned"
    if any(parts[: len(inc)] == inc for inc in includes):
        return "scanned"
    if any(
        len(inc) > len(parts)
        and inc[: len(parts)] == parts
        and not scope.is_excluded(inc)
        for inc in includes
    ):
        return "partial"
    return "outside"


def resolve_start_dir(root: Path, parts: tuple[str, ...]) -> Path | None:
    """``root/parts`` unless a component is a symlink (never followed)."""
    current = Path(root)
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                logger.debug("Skipping symlinked scan folder: %s", current)
                return None
        except OSError:
            return current
    return current


def list_scope_folders(
    roots: Iterable[Path], parent: str, scope: ScanScope
) -> list[dict]:
    """Real subfolders of ``parent`` across all media roots, with scan status.

    Symlinked and internal folders are omitted because the scan never enters
    them. Raises ``ValueError`` for a parent path containing '.' or '..'.
    """
    parent_parts = tuple(_split(normalize_scope_folder(parent))) if parent.strip("/\\ ") else ()
    merged: dict[str, bool] = {}
    for root in roots:
        current = resolve_start_dir(Path(root), parent_parts)
        if current is None:
            continue
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if is_internal_skipped_dir(entry.name):
                continue
            merged[entry.name] = merged.get(entry.name, False) or _has_subfolders(
                entry.path
            )
    folders = []
    for name in sorted(merged, key=lambda n: (n.casefold(), n)):
        rel = "/".join((*parent_parts, name))
        folders.append(
            {
                "name": name,
                "path": rel,
                "has_subfolders": merged[name],
                "status": folder_status(rel, scope),
            }
        )
    return folders


MIN_SEARCH_QUERY_LENGTH = 2


def search_scope_folders(
    roots: Iterable[Path],
    query: str,
    scope: ScanScope,
    *,
    limit: int = 25,
    max_dirs: int = 20000,
    time_budget_s: float = 1.5,
) -> dict:
    """Find folders anywhere under the media roots whose name matches ``query``.

    A query containing ``/`` matches the relative path instead of the name.
    Matching is a case-insensitive substring. The walk is breadth-first, never
    follows symlinks and skips internal folders, but does enter scope-excluded
    folders so a skipped folder can be found and un-skipped.

    Results rank exact match, then prefix, then substring; then shallower
    depth; then name. The walk stops after ``limit`` matches, ``max_dirs``
    scanned folders or ``time_budget_s`` seconds, and ``truncated`` says
    whether anything may have been left out.
    """
    raw = str(query).replace("\\", "/").strip()
    match_path = "/" in raw
    needle = raw.strip("/").casefold()
    if len(needle) < MIN_SEARCH_QUERY_LENGTH:
        return {"folders": [], "truncated": False}

    deadline = time.monotonic() + max(time_budget_s, 0)
    queue: deque[tuple[Path, tuple[str, ...]]] = deque(
        (Path(root), ()) for root in roots
    )
    matches: dict[tuple[str, ...], bool] = {}
    scanned = 0
    truncated = False
    while queue:
        if scanned >= max_dirs or time.monotonic() >= deadline:
            truncated = True
            break
        directory, parts = queue.popleft()
        scanned += 1
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: (e.name.casefold(), e.name))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if is_internal_skipped_dir(entry.name):
                continue
            child = (*parts, entry.name)
            queue.append((Path(entry.path), child))
            haystack = "/".join(child) if match_path else entry.name
            if needle not in haystack.casefold():
                continue
            if child not in matches and len(matches) >= limit:
                truncated = True
                break
            matches[child] = matches.get(child, False) or _has_subfolders(entry.path)
        if truncated:
            break

    def rank(parts: tuple[str, ...]) -> tuple:
        haystack = ("/".join(parts) if match_path else parts[-1]).casefold()
        kind = 0 if haystack == needle else 1 if haystack.startswith(needle) else 2
        return (kind, len(parts), parts[-1].casefold(), "/".join(parts))

    folders = []
    for parts in sorted(matches, key=rank):
        rel = "/".join(parts)
        folders.append(
            {
                "name": parts[-1],
                "path": rel,
                "has_subfolders": matches[parts],
                "status": folder_status(rel, scope),
                "trail": [
                    {
                        "name": parts[i - 1],
                        "path": "/".join(parts[:i]),
                        "status": folder_status("/".join(parts[:i]), scope),
                    }
                    for i in range(1, len(parts))
                ],
            }
        )
    return {"folders": folders, "truncated": truncated}


def _has_subfolders(path: str) -> bool:
    try:
        with os.scandir(path) as it:
            for child in it:
                try:
                    if child.is_dir(follow_symlinks=False) and not is_internal_skipped_dir(
                        child.name
                    ):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False
