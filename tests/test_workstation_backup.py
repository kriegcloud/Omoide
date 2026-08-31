import contextlib
import io
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from scripts import backup_workstation_database as backup


class WorkstationDatabaseBackupTests(unittest.TestCase):
    def make_database(self, data_dir: Path) -> tuple[Path, sqlite3.Connection]:
        database_dir = data_dir / "database"
        database_dir.mkdir(parents=True)
        database_path = database_dir / "omoide.db"
        connection = sqlite3.connect(database_path)
        self.assertEqual(
            connection.execute("PRAGMA journal_mode = WAL").fetchone()[0],
            "wal",
        )
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("CREATE TABLE media (id INTEGER, name TEXT)")
        connection.execute(
            "INSERT INTO media VALUES (?, ?)",
            (1, "committed while WAL is active"),
        )
        connection.commit()
        return database_path, connection

    def test_backup_captures_wal_and_uses_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "portal"
            database_path, source = self.make_database(data_dir)
            try:
                self.assertTrue(
                    database_path.with_name("omoide.db-wal").is_file()
                )

                backup_path = backup.backup_database(data_dir)

                with sqlite3.connect(backup_path) as restored:
                    rows = restored.execute(
                        "SELECT id, name FROM media"
                    ).fetchall()
                self.assertEqual(rows, [(1, "committed while WAL is active")])
                self.assertEqual(
                    stat.S_IMODE(backup_path.stat().st_mode),
                    0o600,
                )
                self.assertEqual(
                    stat.S_IMODE(backup_path.parent.stat().st_mode),
                    0o700,
                )
                self.assertRegex(backup_path.name, backup.BACKUP_NAME_PATTERN)
                self.assertEqual(
                    list(backup_path.parent.glob(".omoide-backup-*.tmp")),
                    [],
                )
            finally:
                source.close()

    def test_retention_removes_only_old_matching_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "portal"
            _, source = self.make_database(data_dir)
            try:
                backup_dir = data_dir / "backups"
                backup_dir.mkdir(mode=0o700)
                oldest = backup_dir / "omoide-20200101T000000.000000Z.db"
                previous = backup_dir / "omoide-20210101T000000.000000Z.db"
                unrelated = backup_dir / "notes.txt"
                matching_directory = (
                    backup_dir / "omoide-20190101T000000.000000Z.db"
                )
                oldest.write_bytes(b"oldest")
                previous.write_bytes(b"previous")
                unrelated.write_text("preserve me", encoding="utf-8")
                matching_directory.mkdir()

                current = backup.backup_database(data_dir, keep=2)

                self.assertFalse(oldest.exists())
                self.assertTrue(previous.exists())
                self.assertTrue(current.exists())
                self.assertTrue(unrelated.exists())
                self.assertTrue(matching_directory.is_dir())
            finally:
                source.close()

    def test_missing_database_fails_clearly_without_creating_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "portal"
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                exit_code = backup.main(["--data-dir", os.fspath(data_dir)])

            self.assertEqual(exit_code, 1)
            self.assertIn("source database does not exist", errors.getvalue())
            self.assertFalse((data_dir / "backups").exists())

    def test_invalid_database_is_never_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "portal"
            database_dir = data_dir / "database"
            database_dir.mkdir(parents=True)
            (database_dir / "omoide.db").write_bytes(b"not a database")

            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                exit_code = backup.main(["--data-dir", os.fspath(data_dir)])

            self.assertEqual(exit_code, 1)
            self.assertIn("backup failed", errors.getvalue())
            self.assertEqual(
                [
                    path
                    for path in (data_dir / "backups").iterdir()
                    if backup.BACKUP_NAME_PATTERN.fullmatch(path.name)
                ],
                [],
            )
            self.assertEqual(
                list((data_dir / "backups").glob(".omoide-backup-*.tmp")),
                [],
            )

    def test_keep_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            with self.assertRaisesRegex(backup.BackupError, "at least 1"):
                backup.backup_database(Path(temporary_dir), keep=0)


if __name__ == "__main__":
    unittest.main()
