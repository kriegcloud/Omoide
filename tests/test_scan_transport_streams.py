"""Ambiguous .ts files are admitted by content, without hiding broken videos."""
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from round6_support import DatabaseCase
from app.models import Media, ProcessingTask
from app.tasks import scan
from sqlmodel import select


class TransportStreamScanTests(DatabaseCase):
    def walk(self):
        return set(scan._walk_media_candidates(
            [self.root], frozenset({'.ts', '.jpg', '.mp4'}), skip_thumbnails=False,
        ))

    def test_text_sources_are_not_candidates_even_in_dependency_and_backup_folders(self):
        for name, content in {
            'core.d.ts': b'export declare class Core {}\n',
            'source.TS': b'\xef\xbb\xbfexport const title = "caf\xc3\xa9";\n',
            'node_modules/package/index.ts': b'// source\nexport default 42;\n',
            'backup/project/plain.ts': b'just some plain text\n',
            'unicode.ts': '// \u65e5\u672c\u8a9e\nexport {};\n'.encode(),
            'unicode-format.ts': '// \u200b\u200c\u200d\U000e0067\u2028\u2029\nexport {};\n'.encode(),
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        photos = {self.root / 'node_modules/photo.jpg', self.root / 'backup/photo.jpg'}
        for path in photos:
            path.write_bytes(b'photo')
        self.assertEqual(self.walk(), photos)

    def test_binary_empty_and_non_ts_candidates_remain_admitted(self):
        paths = set()
        for name, content in {
            'broken.ts': b'\x00\xffbroken video',
            'binary.d.ts': b'\x00\x01\x02',
            'empty.ts': b'',
            'broken.mp4': b'plain text but an unambiguous video extension',
            'control.ts': b'header\x1bpayload',
        }.items():
            path = self.root / name
            path.write_bytes(content)
            paths.add(path)
        self.assertEqual(self.walk(), paths)

    def test_unreadable_candidate_is_left_for_normal_scan_issue_reporting(self):
        path = self.root / 'unreadable.ts'
        path.touch()
        with patch.object(Path, 'open', side_effect=PermissionError('fixture denied')):
            self.assertEqual(self.walk(), {path})

    def test_text_sample_ending_inside_utf8_character_is_still_excluded(self):
        (self.root / 'long.ts').write_bytes(b'/' * 8191 + '\u65e5'.encode() + b'\n')
        self.assertEqual(self.walk(), set())

    def test_ascii_transport_packet_sync_is_preserved(self):
        for size, prefix in ((188, b''), (192, b'AAAA'), (204, b'')):
            with self.subTest(size=size):
                path = self.root / f'packets-{size}.d.ts'
                path.write_bytes((prefix + b'G' + b'A' * (size - len(prefix) - 1)) * 4)
        self.assertEqual(len(self.walk()), 3)

    def test_sniff_is_bounded_and_other_suffixes_are_never_opened(self):
        path = self.root / 'source.ts'
        path.touch()
        with patch.object(Path, 'open') as opened:
            opened.return_value.__enter__.return_value = sample = BytesIO(b'/' * 100_000)
            self.assertEqual(self.walk(), set())
            self.assertEqual(sample.tell(), 8193)
        path.unlink()
        (self.root / 'photo.jpg').touch()
        with patch.object(Path, 'open', side_effect=AssertionError('Unexpected read')):
            self.assertEqual(len(self.walk()), 1)

    def test_real_transport_stream_survives_discovery_regardless_of_declaration_name(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('ffmpeg is required to generate a real transport-stream fixture')
        path = self.root / 'recording.d.TS'
        subprocess.run([
            ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10',
            '-t', '0.3', '-c:v', 'mpeg2video', '-f', 'mpegts', str(path),
        ], check=True, capture_output=True, timeout=30)
        self.assertEqual(self.walk(), {path})

    def test_run_scan_skips_sources_and_keeps_corrupt_video_issue(self):
        source = self.root / 'core.d.ts'
        source.write_text('export declare const core: string;\n')
        broken = self.root / 'broken.ts'
        broken.write_bytes(b'\x00\xffnot a valid stream')
        task = ProcessingTask(task_type='scan', status='pending', total=0, processed=0)
        self.session.add(task)
        self.session.commit()
        # Exercise actual discovery and metadata parsing; isolate only the failed
        # thumbnail decoder so this checks the scanner's issue accounting too.
        with patch.object(scan.db, 'engine', self.engine), \
             patch.object(scan, 'heavy_writer', return_value=nullcontext(True)), \
             patch.object(type(scan.settings.general), 'resolved_media_dirs', return_value=[(self.root, False)]), \
             patch.object(scan, 'generate_thumbnail', return_value=(None, 'Invalid video fixture')):
            scan.run_scan(task.id)
        self.session.refresh(task)
        self.assertEqual(task.status, 'completed')
        self.assertEqual(task.result['discovered'], 1)
        self.assertEqual(task.result['thumbnail_failures'], 1)
        self.assertEqual(task.result['failure_count'], 1)
        rows = self.session.exec(select(Media)).all()
        self.assertEqual([row.path for row in rows], [str(broken)])
