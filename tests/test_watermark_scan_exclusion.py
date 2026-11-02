"""Original backups must never reenter the media catalog, even by explicit scope."""
import tempfile
import unittest
from pathlib import Path

from app.scan_scope import ScanScope
from app.tasks.scan import _walk_media_candidates


class WatermarkBackupScanTests(unittest.TestCase):
    def test_backups_are_excluded_including_explicit_scan_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup = root / '.beep-originals'
            backup.mkdir()
            (backup / 'original.jpg').touch()
            (root / 'visible.jpg').touch()
            def walk(roots, scope=None):
                return list(_walk_media_candidates(roots, frozenset({'.jpg'}), skip_thumbnails=False, scope=scope))
            self.assertEqual(walk([root]), [root / 'visible.jpg'])
            self.assertEqual(walk([backup]), [])
            self.assertEqual(walk([root], ScanScope(include=('.beep-originals',))), [])
