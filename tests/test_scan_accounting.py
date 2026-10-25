"""Scan totals describe database admissions separately from bounded error samples."""
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from round6_support import DatabaseCase
from app.models import Blacklist, Media, ProcessingTask
from app.tasks import scan, state
from app.api.tasks import _to_read, get_task_failures_endpoint
from app.services.task_summary import summarize_task


class ScanAccountingTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(scan.logger, 'warning'))
        self.enterContext(patch.object(scan.logger, 'debug'))
        self.enterContext(patch.object(scan.db, 'engine', self.engine))
        self.enterContext(patch.object(scan, 'heavy_writer', return_value=nullcontext(True)))
        self.enterContext(patch.object(type(scan.settings.general), 'resolved_media_dirs', return_value=[(self.root, False)]))
        self.enterContext(patch.object(scan, '_PROCESS_BATCH_SIZE', 64))

    def task(self):
        row = ProcessingTask(task_type='scan', status='pending', total=0, processed=0)
        self.session.add(row)
        self.session.commit()
        return row

    def test_true_failure_count_exceeds_bounded_details(self):
        for index in range(550):
            state.record_task_failure('scan-accounting-count', str(index), 'missing')
        self.assertEqual(len(state.get_task_failures('scan-accounting-count')), 500)
        self.assertEqual(state.get_failure_count('scan-accounting-count'), 550)

    def test_completed_scan_accounts_for_metadata_and_thumbnail_failures(self):
        task = self.task()
        paths = [self.root / f'missing-{i}.jpg' for i in range(550)]
        paths += [self.root / 'bad-thumbnail.jpg', self.root / 'good.jpg']
        (self.root / 'bad-thumbnail.jpg').touch()

        def parse(path):
            if path.name.startswith('missing-'):
                return None, None, 'Unable to read file information: missing'
            return Media(path=str(path), filename=path.name, size=1), None, None

        def thumbnail(media):
            return (None, 'Decoder failed') if media.filename == 'bad-thumbnail.jpg' else ('good.jpg', None)

        with patch.object(scan, '_walk_media_candidates', return_value=iter(paths)), \
             patch.object(scan, 'process_file', side_effect=parse), \
             patch.object(scan, 'generate_thumbnail', side_effect=thumbnail):
            scan.run_scan(task.id)
        self.session.refresh(task)
        self.assertEqual(task.status, 'completed')
        self.assertEqual((task.processed, task.total), (552, 552))
        self.assertEqual(task.result['discovered'], 552)
        self.assertEqual(task.result['indexed'], 2)
        self.assertEqual(task.result['skipped'], 550)
        self.assertEqual(task.result['metadata_failures'], 550)
        self.assertEqual(task.result['thumbnail_failures'], 1)
        self.assertEqual(task.result['failure_count'], 551)
        self.assertEqual(task.result['missing_before_indexing'], 550)
        self.assertEqual(task.result['missing_during_thumbnail'], 0)
        self.assertTrue(task.result['failure_samples'][0]['reason'].startswith('Missing before indexing:'))
        self.assertTrue(task.result['failure_samples'][-1]['reason'].startswith('Thumbnail generation failed:'))
        self.assertEqual(task.result['indexed'] + task.result['skipped'], task.total)
        self.assertEqual(len(task.result['failure_samples']), 500)
        self.assertIn('2 indexed', summarize_task(task))
        self.assertIn('550 skipped', summarize_task(task))
        self.assertIn('1 thumbnail failure', summarize_task(task))
        self.assertNotIn('failure_samples', _to_read(task).result)
        self.assertEqual(len(task.result['failure_samples']), 500)
        # Counts and bounded examples remain available after in-memory eviction/restart.
        state._task_failures.clear()
        self.assertEqual(_to_read(task).failure_count, 551)
        self.assertEqual(len(get_task_failures_endpoint(task.id, self.session)), 500)

    def test_unreadable_directory_totals_are_not_file_skips_or_capped(self):
        task = self.task()

        def walk(*args, on_error, **kwargs):
            for index in range(550):
                on_error(PermissionError(13, 'Access denied', str(self.root / str(index))))
            return iter(())

        with patch.object(scan, '_walk_media_candidates', side_effect=walk):
            scan.run_scan(task.id)
        self.session.refresh(task)
        self.assertEqual((task.processed, task.total), (0, 0))
        self.assertEqual(task.result['skipped'], 0)
        self.assertEqual(task.result['unreadable_directory_count'], 550)
        self.assertLessEqual(len(task.result['unreadable_dirs']), 500)
        self.assertEqual(task.result['failure_count'], 550)
        self.assertIn('550 unreadable directories', summarize_task(task))
        payload = _to_read(task)
        self.assertNotIn('unreadable_dirs', payload.result)
        self.assertEqual(payload.result['unreadable_directory_count'], 550)
        self.assertEqual(len(task.result['unreadable_dirs']), 500)
        self.assertEqual(len(get_task_failures_endpoint(task.id, self.session)), 500)


    def test_blacklist_and_uniqueness_races_are_skips_without_false_failures(self):
        task = self.task()
        blacklisted = self.root / 'blacklisted.jpg'
        duplicate = self.root / 'duplicate.jpg'
        existing = self.media(path='/outside-current-root/duplicate.jpg')
        existing_path = existing.path

        class ImmediatePool:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def map(pool, function, items):
                return [function(item) for item in items]

        def parse(path):
            if path == blacklisted:
                with scan.Session(self.engine) as session:
                    session.add(Blacklist(path=str(path)))
                    session.commit()
            row_path = str(path) if path == blacklisted else existing_path
            return Media(path=row_path, filename=path.name, size=1), None, None

        with patch.object(scan, '_walk_media_candidates', return_value=iter([blacklisted, duplicate])),              patch.object(scan, 'process_file', side_effect=parse),              patch.object(scan, 'ThreadPoolExecutor', ImmediatePool),              patch.object(scan, 'generate_thumbnail') as thumbnail:
            scan.run_scan(task.id)
        self.session.refresh(task)
        self.assertEqual((task.processed, task.total), (2, 2))
        self.assertEqual(task.result['indexed'], 0)
        self.assertEqual(task.result['skipped'], 2)
        self.assertEqual(task.result['blacklisted'], 1)
        self.assertEqual(task.result['already_indexed'], 1)
        self.assertEqual(task.result['failure_count'], 0)
        thumbnail.assert_not_called()

    def test_interruption_persists_only_admitted_outcomes_and_remaining_work(self):
        task = self.task()
        paths = [self.root / f'{i}.jpg' for i in range(3)]
        calls = []

        class ImmediatePool:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def map(pool, function, items):
                return [function(item) for item in items]

        def parse(path):
            calls.append(path)
            if len(calls) == 3:
                with scan.Session(self.engine) as session:
                    row = session.get(ProcessingTask, task.id)
                    row.status = 'interrupted'
                    session.add(row)
                    session.commit()
            return Media(path=str(path), filename=path.name, size=1), None, None

        with patch.object(scan, '_walk_media_candidates', return_value=iter(paths)),              patch.object(scan, '_PROCESS_BATCH_SIZE', 2),              patch.object(scan, 'process_file', side_effect=parse),              patch.object(scan, 'ThreadPoolExecutor', ImmediatePool),              patch.object(scan, 'generate_thumbnail', return_value=('thumb.jpg', None)):
            scan.run_scan(task.id)
        self.session.refresh(task)
        self.assertEqual(task.status, 'interrupted')
        self.assertEqual((task.processed, task.total), (2, 3))
        self.assertEqual(task.result['indexed'], 2)
        self.assertEqual(task.result['skipped'], 0)
        self.assertEqual(task.result['remaining'], 1)
        self.assertEqual(task.result['failure_count'], 0)
