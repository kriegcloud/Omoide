"""Person observations use both signals and keep unavailable work pending."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image


class PersonDetectionTests(unittest.TestCase):
    def test_both_signals_and_threshold(self):
        from app.processors.person_detection import has_persons
        self.assertFalse(has_persons(.349, 0))
        self.assertTrue(has_persons(.35, -1))
        self.assertTrue(has_persons(0, .001))

    def test_fake_client_stored_vector_and_text_cache(self):
        from app.processors import person_detection as pd
        pd._text_embeddings.clear()
        model = SimpleNamespace(hf_repo='fake-siglip', embedding_size=2)
        client = Mock(enabled=True)
        client.embed_texts.return_value = np.array([[1., 0.]] * 9 + [[0., 1.]] * 9)
        client.detect_persons.return_value = [{'score': .1, 'boxes': []}]
        session = Mock()
        session.exec.return_value.first.return_value = (np.array([0., 1.], dtype=np.float32).tobytes(),)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            Image.new('RGB', (8, 8)).save(path)
            media = SimpleNamespace(id=1, path=str(path), duration=None, persons_detected=False)
            with patch.object(pd, 'get_vision_client', return_value=client), patch.object(pd, 'safe_commit'), patch.object(pd.settings.ai, 'clip_model', model):
                self.assertTrue(pd.PersonDetectionProcessor().process(media, session, []))
                self.assertFalse(media.has_persons)
                self.assertEqual(media.person_siglip_margin, -1)
                media.persons_detected = False
                pd.PersonDetectionProcessor().process(media, session, [])
            self.assertEqual(client.embed_texts.call_count, 1)

    def test_missing_embedding_leaves_pending(self):
        from app.processors import person_detection as pd
        media = SimpleNamespace(id=1, persons_detected=False)
        session = Mock()
        session.exec.return_value.first.return_value = None
        with patch.object(pd, 'get_vision_client', return_value=Mock(enabled=True)):
            self.assertTrue(pd.PersonDetectionProcessor().process(media, session, []))
        self.assertFalse(media.persons_detected)

    def test_worker_failure_does_not_write_partial_observations(self):
        from app.processors import person_detection as pd
        from app.services.vision_client import VisionError
        model = SimpleNamespace(hf_repo='fake-unavailable', embedding_size=2)
        client = Mock(enabled=True)
        client.embed_texts.return_value = np.array([[1., 0.]] * 9 + [[0., 1.]] * 9)
        client.detect_persons.side_effect = VisionError('offline')
        session = Mock()
        session.exec.return_value.first.return_value = ([1., 0.],)
        media = SimpleNamespace(id=1, persons_detected=False)
        with patch.object(pd, 'get_vision_client', return_value=client), patch.object(pd.settings.ai, 'clip_model', model), patch.object(pd, 'detector_images', return_value=[Image.new('RGB', (8, 8))]):
            self.assertTrue(pd.PersonDetectionProcessor().process(media, session, []))
        self.assertFalse(media.persons_detected)
        session.add.assert_not_called()

    def test_video_uses_at_most_five_existing_frames(self):
        from app.processors.person_detection import detector_images
        media = SimpleNamespace(duration=30)
        scenes = [(None, np.zeros((8, 8, 3), dtype=np.uint8)) for _ in range(12)]
        self.assertEqual(len(detector_images(media, scenes)), 5)

    def test_nopersons_excludes_unscanned_and_faces_are_independent(self):
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel, Session, select
        from app.models import Media
        from app.api.nopersons import _base_filter
        from app.api.nofaces import _base_filter as nofaces_filter
        engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for i, detected, present, faces in [(1, True, False, False), (2, True, True, True), (3, False, None, False)]:
                session.add(Media(id=i, filename=f'{i}.png', path=f'/{i}.png', size=1, persons_detected=detected, has_persons=present, faces_extracted=faces))
            session.commit()
            self.assertEqual([m.id for m in session.exec(select(Media).where(_base_filter(None, 'processed')))], [1])
            self.assertEqual([m.id for m in session.exec(select(Media).where(_base_filter(None, 'pending')))], [3])
            self.assertEqual([m.id for m in session.exec(select(Media).where(nofaces_filter(None, 'processed')))], [2])


from round6_support import DatabaseCase


class PersonDetectionApiTests(DatabaseCase):
    def fixture_rows(self):
        from datetime import datetime
        folder = str(self.root / 'album')
        confirmed = self.media(path=folder + '/confirmed.jpg', persons_detected=True,
                               has_persons=False, person_score=.1, person_siglip_margin=-.2)
        pending = self.media(path=folder + '/pending.jpg')
        positive = self.media(path=folder + '/positive.jpg', persons_detected=True, has_persons=True)
        missing = self.media(path=folder + '/missing.jpg', persons_detected=True,
                             has_persons=False, missing_since=datetime.now())
        sibling = self.media(path=folder + '-other/other.jpg', persons_detected=True, has_persons=False)
        self.media(path=folder + '/missing-pending.jpg', missing_since=datetime.now())
        self.media(path=folder + '-other/pending.jpg')
        video = self.media(path=folder + '/video.mp4', duration=10, persons_detected=True, has_persons=False)
        return folder, confirmed, pending, positive, missing, sibling, video

    def test_listing_response_scope_folder_missing_and_media_type(self):
        from app.api.nopersons import get_no_persons_media
        folder, confirmed, pending, _, _, _, video = self.fixture_rows()
        args = dict(session=self.session, cursor=None, limit=1, folder=folder, media_type=None)
        first = get_no_persons_media(**args, scope='processed')
        self.assertEqual(first.total, 2)
        self.assertEqual([item.id for item in first.items], [video.id])
        item = get_no_persons_media(**{**args, 'media_type': 'image'}, scope='processed').model_dump()['items'][0]
        self.assertFalse(item['has_persons'])
        self.assertTrue(item['persons_detected'])
        self.assertEqual(item['person_score'], .1)
        self.assertEqual(item['person_siglip_margin'], -.2)
        self.assertIn('faces_extracted', item)
        args['cursor'] = first.next_cursor
        second = get_no_persons_media(**args, scope='processed', include_total=False)
        self.assertEqual([item.id for item in second.items], [confirmed.id])
        self.assertIsNone(second.total)
        args.update(cursor=None, media_type='image')
        images = get_no_persons_media(**args, scope='processed')
        self.assertEqual(images.total, 1)
        waiting = get_no_persons_media(**args, scope='pending')
        self.assertEqual([item.id for item in waiting.items], [pending.id])
        self.assertFalse(waiting.items[0].persons_detected)
        self.assertIsNone(waiting.items[0].has_persons)

    def test_resolve_select_all_only_confirmed_visible_scoped_images(self):
        from app.api.nopersons import resolve_no_persons
        from app.schemas.nopersons import NoPersonsResolveRequest
        from app.models import Media
        folder, confirmed, pending, positive, missing, sibling, video = self.fixture_rows()
        retained_ids = [row.id for row in (pending, positive, missing, sibling, video)]
        target_id = confirmed.id
        result = resolve_no_persons(NoPersonsResolveRequest(
            action='DELETE_RECORDS', select_all=True, folder=folder, media_type='image'), self.session)
        self.assertEqual(result['processed_ids'], [target_id])
        self.assertEqual(result['errors'], [])
        self.assertIsNone(self.session.get(Media, target_id))
        self.assertTrue(all(self.session.get(Media, media_id) is not None for media_id in retained_ids))

    def test_resolve_pending_rechecks_ids_and_excludes_stale_selection(self):
        from app.api.nopersons import resolve_no_persons
        from app.schemas.nopersons import NoPersonsResolveRequest
        from app.models import Media
        folder, confirmed, pending, positive, missing, sibling, video = self.fixture_rows()
        pending_id, confirmed_id = pending.id, confirmed.id
        result = resolve_no_persons(NoPersonsResolveRequest(
            action='DELETE_RECORDS', scope='pending', folder=folder,
            media_ids=[pending_id, confirmed_id, positive.id, missing.id, sibling.id, video.id]), self.session)
        self.assertEqual(result['processed_ids'], [pending_id])
        self.assertIn(confirmed_id, result['skipped_ids'])
        self.assertEqual(result['removed'], 1)
        self.assertIsNotNone(self.session.get(Media, confirmed_id))

    def test_config_activation_and_real_pipeline_pending_filter(self):
        from app.config import settings
        from app.models import Media
        from app.processors.person_detection import PersonDetectionProcessor
        from app.tasks import media_processing as processing
        from sqlmodel import select
        scanned = self.media(persons_detected=True, has_persons=False)
        pending = self.media()
        processor = PersonDetectionProcessor()
        with patch.object(settings.processors, 'person_detection_processor_active', False):
            processor.load_model()
            self.assertFalse(processor.active)
            self.assertNotIn('person_detection', processing.edit_processor_names())
            with patch.object(processing, 'processors', [processor]):
                self.assertEqual(processing._media_processing_conditions(), [])
        with patch.object(settings.processors, 'person_detection_processor_active', True):
            processor.load_model()
            self.assertTrue(processor.active)
            self.assertIn('person_detection', processing.edit_processor_names())
            with patch.object(processing, 'processors', [processor]):
                self.assertEqual(processing._count_media_to_process(self.session), 2)
                # A scanned row with no extracted scenes still needs scene extraction.
                scanned.extracted_scenes = True
                pending.extracted_scenes = True
                self.session.add(scanned); self.session.add(pending); self.session.commit()
                self.assertEqual(processing._count_media_to_process(self.session), 1)
        ids = self.session.exec(select(Media.id).where(processor.get_pending_condition())).all()
        self.assertEqual(ids, [pending.id])


if __name__ == '__main__':
    unittest.main()
