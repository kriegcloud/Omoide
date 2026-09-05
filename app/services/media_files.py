import errno
import hashlib
import os
import shutil
from pathlib import Path


MediaRoot = tuple[Path, bool]


class MediaFileError(Exception):
    pass


class InvalidMediaPathError(MediaFileError):
    pass


class ReadOnlyMediaRootError(MediaFileError):
    pass


class MediaFileMissingError(MediaFileError):
    pass


class MediaFileCollisionError(MediaFileError):
    pass


def _resolved_roots(media_roots: list[MediaRoot]) -> list[MediaRoot]:
    return [(Path(root).resolve(), read_only) for root, read_only in media_roots]


def _root_for(path: Path, media_roots: list[MediaRoot]) -> MediaRoot | None:
    resolved = path.resolve(strict=False)
    matches: list[MediaRoot] = []
    for root, read_only in _resolved_roots(media_roots):
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        matches.append((root, read_only))
    return max(matches, key=lambda item: len(item[0].parts), default=None)


def require_writable_media_file(
    source_path: str | Path, media_roots: list[MediaRoot]
) -> Path:
    """Resolve a media file and require it to live in a writable root."""
    source = Path(source_path).expanduser().resolve(strict=False)
    source_root = _root_for(source, media_roots)
    if source_root is None:
        raise InvalidMediaPathError("Media file is outside the configured media roots")
    _, read_only = source_root
    if read_only:
        raise ReadOnlyMediaRootError("Source media directory is read-only")
    if not source.is_file():
        raise MediaFileMissingError("Media file is missing")
    return source


def resolve_destination_dir(
    destination_dir: str,
    media_roots: list[MediaRoot],
    *,
    preferred_root: Path | None = None,
) -> Path:
    requested = Path(destination_dir).expanduser()
    candidates: list[Path]
    if requested.is_absolute():
        candidates = [requested]
    else:
        roots = _resolved_roots(media_roots)
        if preferred_root is not None:
            preferred = preferred_root.resolve()
            roots.sort(key=lambda item: item[0] != preferred)
        candidates = [root / requested for root, _ in roots]

    read_only_match = False
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        root_match = _root_for(resolved, media_roots)
        if root_match is None:
            continue
        _, read_only = root_match
        if read_only:
            read_only_match = True
            continue
        if resolved.is_dir():
            return resolved

    if read_only_match:
        raise ReadOnlyMediaRootError("Destination media directory is read-only")
    raise InvalidMediaPathError(
        "Destination must be an existing directory inside a writable media root"
    )


def validate_filename(filename: str, current_filename: str) -> str:
    value = filename.strip()
    if (
        not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise InvalidMediaPathError("Filename must be a basename without separators")
    if not Path(value).suffix:
        value += Path(current_filename).suffix
    return value


def _verify_copy(source: Path, target: Path) -> None:
    if source.stat().st_size != target.stat().st_size:
        raise OSError("Copied file size does not match source")
    source_hash = hashlib.sha256()
    target_hash = hashlib.sha256()
    with source.open("rb") as source_file, target.open("rb") as target_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            source_hash.update(chunk)
        for chunk in iter(lambda: target_file.read(1024 * 1024), b""):
            target_hash.update(chunk)
    if source_hash.digest() != target_hash.digest():
        raise OSError("Copied file contents do not match source")


def _replace_with_fallback(source: Path, target: Path) -> None:
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        try:
            shutil.copy2(source, target)
            _verify_copy(source, target)
            source.unlink()
        except Exception:
            target.unlink(missing_ok=True)
            raise


def move_media_file(
    source_path: str | Path,
    destination_dir: str,
    media_roots: list[MediaRoot],
) -> Path:
    source = require_writable_media_file(source_path, media_roots)
    root, _ = _root_for(source, media_roots)  # validated above

    destination = resolve_destination_dir(
        destination_dir, media_roots, preferred_root=root
    )
    target = destination / source.name
    if target == source:
        return source
    if target.exists():
        raise MediaFileCollisionError("Target file already exists")
    _replace_with_fallback(source, target)
    return target


def rename_media_file(
    source_path: str | Path,
    filename: str,
    media_roots: list[MediaRoot],
) -> Path:
    source = require_writable_media_file(source_path, media_roots)

    target = source.with_name(validate_filename(filename, source.name))
    if target == source:
        return source
    if target.exists():
        raise MediaFileCollisionError("Target file already exists")
    _replace_with_fallback(source, target)
    return target


def create_media_folder(
    parent_path: str,
    name: str,
    media_roots: list[MediaRoot],
) -> Path:
    folder_name = validate_filename(name, "")
    parent = resolve_destination_dir(parent_path, media_roots)
    target = parent / folder_name
    if target.exists():
        raise MediaFileCollisionError("Folder already exists")
    target.mkdir()
    return target
