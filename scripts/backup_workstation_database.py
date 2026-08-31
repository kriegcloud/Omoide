#!/usr/bin/env python3
"""Create a durable, consistent backup of the workstation SQLite database."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_KEEP = 14
DATABASE_RELATIVE_PATH = Path("database/omoide.db")
BACKUP_DIRECTORY_NAME = "backups"
BACKUP_NAME_PATTERN = re.compile(
    r"^omoide-(?P<timestamp>\d{8}T\d{6}\.\d{6}Z)\.db$"
)


class BackupError(RuntimeError):
    """Raised when a safe database backup cannot be completed."""


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _database_uri(database_path: Path) -> str:
    """Return a SQLite URI that cannot create or write the source database."""
    return f"{database_path.resolve(strict=True).as_uri()}?mode=ro"


def _prepare_backup_directory(data_dir: Path) -> Path:
    backup_dir = data_dir / BACKUP_DIRECTORY_NAME
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise BackupError(f"backup path is not a directory: {backup_dir}")
    backup_dir.chmod(0o700)
    return backup_dir


@contextmanager
def _backup_lock(backup_dir: Path) -> Iterator[None]:
    lock_path = backup_dir / ".backup.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _next_backup_path(backup_dir: Path) -> Path:
    for _ in range(1_000):
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        candidate = backup_dir / f"omoide-{timestamp}.db"
        if not os.path.lexists(candidate):
            return candidate
    raise BackupError("could not allocate a unique timestamped backup name")


def _quick_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if rows != [("ok",)]:
        details = "; ".join(str(row[0]) for row in rows) or "no result"
        raise BackupError(f"backup failed SQLite quick_check: {details}")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _matching_backups(backup_dir: Path) -> list[tuple[str, Path]]:
    backups: list[tuple[str, Path]] = []
    with os.scandir(backup_dir) as entries:
        for entry in entries:
            match = BACKUP_NAME_PATTERN.fullmatch(entry.name)
            if match is None or not entry.is_file(follow_symlinks=False):
                continue
            backups.append((match.group("timestamp"), Path(entry.path)))
    backups.sort(key=lambda backup: backup[0], reverse=True)
    return backups


def _apply_retention(backup_dir: Path, keep: int) -> bool:
    removed = False
    for _, backup_path in _matching_backups(backup_dir)[keep:]:
        backup_path.unlink()
        removed = True
    return removed


def backup_database(data_dir: Path, *, keep: int = DEFAULT_KEEP) -> Path:
    """Back up ``<data_dir>/database/omoide.db`` and return the new path."""
    if keep < 1:
        raise BackupError("keep must be at least 1")

    database_path = data_dir / DATABASE_RELATIVE_PATH
    if not database_path.is_file():
        raise BackupError(f"source database does not exist: {database_path}")

    backup_dir = _prepare_backup_directory(data_dir)
    with _backup_lock(backup_dir):
        final_path = _next_backup_path(backup_dir)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=backup_dir,
            prefix=".omoide-backup-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)

        try:
            with closing(
                sqlite3.connect(
                    _database_uri(database_path),
                    uri=True,
                    timeout=30.0,
                )
            ) as source:
                source.execute("PRAGMA query_only = ON")
                source.execute("PRAGMA busy_timeout = 30000")
                with closing(
                    sqlite3.connect(temporary_path, timeout=30.0)
                ) as destination:
                    source.backup(destination)
                    _quick_check(destination)

            temporary_path.chmod(0o600)
            _fsync_file(temporary_path)
            os.replace(temporary_path, final_path)
            _fsync_directory(backup_dir)

            if _apply_retention(backup_dir, keep):
                _fsync_directory(backup_dir)
            return final_path
        finally:
            temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a consistent, atomic backup of an Omoide workstation "
            "database."
        )
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="Omoide data directory containing database/omoide.db",
    )
    parser.add_argument(
        "--keep",
        default=DEFAULT_KEEP,
        type=_positive_integer,
        help=f"number of newest backups to retain (default: {DEFAULT_KEEP})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        backup_path = backup_database(args.data_dir, keep=args.keep)
    except (BackupError, OSError, sqlite3.Error) as error:
        print(f"backup failed: {error}", file=sys.stderr)
        return 1
    print(backup_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
