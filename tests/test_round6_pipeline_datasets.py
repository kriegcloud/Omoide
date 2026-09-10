"""Audit regressions for dataset rendering and bounded item pagination."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import delete_dataset, get_analysis, list_captions, list_items
from app.models import (
    AnnotationAuthor, AnnotationKind, AnnotationReviewStatus, DatasetItem,
    Media, MediaAnnotation, Person, TrainingDataset, TrainingDatasetKind,
)
from app.schemas.dataset import DatasetCreate, DatasetUpdate
from app.services.datasets import render_caption
from app.services.curation import compute_item_metrics, dataset_gaps


class DatasetTemplateAuditTests(unittest.TestCase):
    def test_create_and_update_reject_invalid_caption_templates(self):
        for template in ('{unknown}', '{caption', '{}', '{0}', '{caption.upper}',
                         '{caption[0]}', '{caption:d}', '{caption!q}', '{caption:{unknown}}', '{caption:{class}}'):
            for schema, kwargs in ((DatasetCreate, {'name': 'Test'}), (DatasetUpdate, {})):
                with self.subTest(template=template, schema=schema.__name__):
                    with self.assertRaises(ValidationError):
                        schema(caption_template=template, **kwargs)

    def test_valid_caption_templates_preserve_literals_and_supported_fields(self):
        template = '{{literal}} {trigger} {class} {caption!s:>8}'
        self.assertEqual(DatasetCreate(name='Test', caption_template=template).caption_template, template)
        self.assertIsNone(DatasetUpdate().caption_template)

    def test_name_substitution_matches_whole_tokens(self):
        dataset = TrainingDataset(name='Test', slug='test', trigger_word='subjectx',
                                  class_token='person', caption_template='{caption}')
        caption = render_caption(dataset, "Ann standing by a banner with Joanne and ANN's friend", Person(name='Ann'))
        self.assertEqual(caption, "subjectx standing by a banner with Joanne and subjectx's friend")

    def test_name_substitution_treats_replacement_as_literal(self):
        dataset = TrainingDataset(name='Test', slug='test', trigger_word=r'subject\1',
                                  class_token='person', caption_template='{caption}')
        self.assertEqual(render_caption(dataset, 'Ann outside', Person(name='Ann')), r'subject\1 outside')

    def test_name_substitution_does_not_replace_the_new_trigger_again(self):
        dataset = TrainingDataset(name='Test', slug='test', trigger_word='jane-doe-x',
                                  class_token='person', caption_template='{caption}')
        self.assertEqual(render_caption(dataset, 'Jane Doe outside', Person(name='Jane Doe')), 'jane-doe-x outside')


class DatasetServiceAuditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, _record):
            connection.execute('PRAGMA foreign_keys=ON')
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def dataset_items(self, session, count=1):
        dataset = TrainingDataset(name='Test', slug='test', trigger_word='subjectx', class_token='person')
        session.add(dataset)
        session.flush()
        items = []
        for index in range(count):
            media = Media(path=f'/test/{index}.jpg', filename=f'{index}.jpg', size=1, width=100, height=100)
            session.add(media)
            session.flush()
            item = DatasetItem(dataset_id=dataset.id, media_id=media.id, position=index // 2)
            session.add(item)
            items.append(item)
        session.commit()
        return dataset, items

    def test_caption_metadata_identifies_selected_approved_revision(self):
        with Session(self.engine) as session:
            dataset, items = self.dataset_items(session)
            approved = MediaAnnotation(media_id=items[0].media_id, revision=1,
                kind=AnnotationKind.CAPTION, author=AnnotationAuthor.MACHINE,
                review_status=AnnotationReviewStatus.APPROVED, content={'caption': 'approved outdoor portrait'})
            candidate = MediaAnnotation(media_id=items[0].media_id, revision=2,
                kind=AnnotationKind.CAPTION, author=AnnotationAuthor.MACHINE,
                review_status=AnnotationReviewStatus.CANDIDATE, content={'caption': 'new candidate portrait'})
            session.add_all([approved, candidate])
            session.commit()
            row = list_captions(dataset.id, 'all', None, 100, session).items[0]
            self.assertEqual(row.caption, 'approved outdoor portrait')
            self.assertEqual(row.annotation_id, approved.id)
            self.assertEqual(row.review_status, AnnotationReviewStatus.APPROVED)
            self.assertEqual(len(list_captions(dataset.id, 'approved', None, 100, session).items), 1)
            self.assertEqual(list_captions(dataset.id, 'candidate', None, 100, session).items, [])

    def test_position_pagination_does_not_analyze_the_dataset(self):
        with Session(self.engine) as session:
            dataset, items = self.dataset_items(session, count=7)
            with patch('app.api.datasets.compute_dataset_analysis', side_effect=AssertionError('whole dataset analysis')):
                first = list_items(dataset.id, None, 2, True, 'position', session)
                second = list_items(dataset.id, first.next_cursor, 2, True, 'position', session)
            self.assertEqual([row.id for row in first.items], [items[0].id, items[1].id])
            self.assertEqual([row.id for row in second.items], [items[2].id, items[3].id])
            self.assertEqual(second.next_cursor, '4')

    def test_position_pagination_computes_metrics_only_for_the_sql_page(self):
        with Session(self.engine) as session:
            dataset, items = self.dataset_items(session, count=7)
            queries = []
            def capture(_conn, _cursor, statement, _params, _context, _many):
                queries.append(statement)
            event.listen(self.engine, 'before_cursor_execute', capture)
            try:
                with patch('app.api.datasets.compute_item_metrics', return_value=[]) as metrics:
                    page = list_items(dataset.id, '2', 2, True, 'position', session)
                self.assertEqual([row.id for row in page.items], [items[2].id, items[3].id])
                self.assertEqual([item.id for item in metrics.call_args.args[2]], [items[2].id, items[3].id])
                item_queries = [sql for sql in queries if 'FROM datasetitem' in sql]
                self.assertEqual(len(item_queries), 1)
                self.assertIn('LIMIT', item_queries[0])
            finally:
                event.remove(self.engine, 'before_cursor_execute', capture)

    def test_gap_request_does_not_repeat_clustering_and_duplicate_analysis(self):
        with Session(self.engine) as session:
            dataset, _ = self.dataset_items(session)
            with patch('app.services.curation._duplicate_groups', side_effect=AssertionError('repeated quadratic duplicate analysis')):
                gaps = dataset_gaps(session, dataset)
            self.assertTrue(all('candidates' in gap for gap in gaps))

    def test_analysis_and_gap_candidates_share_one_analysis(self):
        with Session(self.engine) as session:
            dataset, _ = self.dataset_items(session)
            with patch('app.services.curation.compute_item_metrics', wraps=compute_item_metrics) as metrics:
                result = get_analysis(dataset.id, session, include_gap_candidates=True)
            self.assertEqual(metrics.call_count, 1)
            self.assertTrue(all('candidates' in gap for gap in result['gaps']))

    def test_referenced_regularization_delete_returns_409_without_changes(self):
        with Session(self.engine) as session:
            regularization = TrainingDataset(name='Regularization', slug='reg', trigger_word='', class_token='person', kind=TrainingDatasetKind.REGULARIZATION)
            session.add(regularization)
            session.flush()
            subject = TrainingDataset(name='Subject', slug='subject', trigger_word='subjectx', class_token='person', regularization_dataset_id=regularization.id)
            session.add(subject)
            session.commit()
            with self.assertRaises(HTTPException) as raised:
                delete_dataset(regularization.id, session)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIsNotNone(session.get(TrainingDataset, regularization.id))
            self.assertEqual(session.get(TrainingDataset, subject.id).regularization_dataset_id, regularization.id)


if __name__ == '__main__':
    unittest.main()
