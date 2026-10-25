"""Person exports persist exact outcomes independently of their bounded samples."""
from unittest.mock import patch
from round6_support import DatabaseCase
from app.models import Media, PersonMediaLink, ProcessingTask
from app.tasks import export, state
from app.api.tasks import _to_read, get_task_failures_endpoint

class PersonExportAccountingTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(export.db, 'engine', self.engine))

    def run_export(self, outcomes, cancel_after_first=False):
        person = self.person()
        rows = [Media(path=f'/fixture/{i}.jpg', filename=f'{i}.jpg', size=1) for i in range(len(outcomes))]
        self.session.add_all(rows)
        self.session.commit()
        self.session.add_all([PersonMediaLink(person_id=person.id, media_id=row.id) for row in rows])
        task = ProcessingTask(task_type='export_person_media', status='pending', total=0, processed=0)
        self.session.add(task)
        self.session.commit()
        remaining = iter(outcomes)
        def process(session, *_args):
            result = next(remaining)
            if isinstance(result, Exception):
                raise result
            if cancel_after_first:
                active = session.get(ProcessingTask, task.id)
                active.status = 'cancelled'
                session.add(active)
                session.commit()
            return result
        with patch.object(export, '_export_one', side_effect=process):
            export.run_person_media_export(task.id, person.id, self.root, 'copy')
        self.session.refresh(task)
        return task

    def test_more_than_500_failures_are_persisted_with_bounded_samples(self):
        task = self.run_export(['Missing'] * 550)
        self.assertEqual(task.result['exported'], 0)
        self.assertEqual(task.result['skipped'], 550)
        self.assertEqual(task.result['failure_count'], 550)
        self.assertEqual(len(task.result['failure_samples']), 500)
        self.assertEqual((task.processed, task.total), (550, 550))
        state._task_failures.clear()
        self.assertEqual(_to_read(task).failure_count, 550)
        self.assertEqual(len(get_task_failures_endpoint(task.id, self.session)), 500)

    def test_mixed_and_empty_outcomes_are_exact(self):
        task = self.run_export([None, 'Unreadable', None])
        self.assertEqual((task.result['exported'], task.result['skipped']), (2, 1))
        self.assertEqual(task.result['attempted'], 3)
        self.assertEqual(task.result['remaining'], 0)
        self.assertEqual(self.run_export([]).result['exported'], 0)

    def test_cancelled_export_retains_partial_counts_without_claiming_pending_items(self):
        task = self.run_export([None, None, None], cancel_after_first=True)
        self.assertEqual(task.status, 'cancelled')
        self.assertEqual(task.result['attempted'], 1)
        self.assertEqual(task.result['remaining'], 2)
        self.assertEqual((task.result['exported'], task.result['skipped']), (1, 0))

    def test_exception_without_message_is_still_a_failed_export(self):
        task = self.run_export([RuntimeError()])
        self.assertEqual((task.result['exported'], task.result['skipped']), (0, 1))
        self.assertEqual(task.result['failure_samples'][0]['reason'], 'RuntimeError')
