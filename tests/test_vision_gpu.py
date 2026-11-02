"""GPU vision routing without GPU packages, network, or bound sockets."""
import base64
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine


class VisionClientTests(unittest.TestCase):
    def test_fragmented_framing(self):
        from app.services.vision_client import read_message, write_message
        conn = Mock()
        payload = json.dumps({'ok': True}).encode()
        raw = struct.pack('!I', len(payload)) + payload
        conn.recv.side_effect = [raw[:2], raw[2:4], raw[4:7], raw[7:]]
        self.assertEqual(read_message(conn), {'ok': True})
        write_message(conn, {'ok': True})
        self.assertEqual(conn.sendall.call_args.args[0][:4], struct.pack('!I', 11))

    def test_reject_oversize_and_truncated(self):
        from app.services.vision_client import read_message, VisionError, MAX_REQUEST_BYTES
        for chunks in ([struct.pack('!I', MAX_REQUEST_BYTES + 1)], [b'']):
            conn = Mock(); conn.recv.side_effect = chunks
            with self.assertRaises(VisionError):
                read_message(conn)

    def test_health_cached_and_model_checked(self):
        from app.services.vision_client import VisionClient, PROTOCOL_VERSION
        client = VisionClient('/unused')
        client.request = Mock(return_value={'ok': True, 'protocol': PROTOCOL_VERSION, 'model_id': 'wanted', 'embed_dim': 2})
        self.assertTrue(client.available('wanted', 2))
        self.assertTrue(client.available('wanted', 2))
        self.assertFalse(client.available('other', 2))
        self.assertEqual(client.request.call_count, 1)
        client.invalidate()
        self.assertFalse(client.available('wanted', 2))
        self.assertEqual(client.request.call_count, 1)
        client._expires = 0
        self.assertTrue(client.available('wanted', 2))
        self.assertEqual(client.request.call_count, 2)

    def test_batch_vectors_and_validation(self):
        from app.services.vision_client import VisionClient, VisionError
        client = VisionClient('/unused')
        encoded = base64.b64encode(np.array([.6, .8], dtype='<f4').tobytes()).decode()
        client.request = Mock(side_effect=lambda action, **kw: {'vectors': [encoded] * len(kw['texts']), 'model_id': 'm'})
        result = client.embed_texts(['a'] * 65, 'm', 2)
        self.assertEqual(result.shape, (65, 2))
        self.assertEqual(client.request.call_count, 2)
        client.request = Mock(return_value={'vectors': [encoded], 'model_id': 'wrong'})
        with self.assertRaises(VisionError):
            client.embed_texts(['a'], 'm', 2)

    def test_quality_rejects_nonfinite_or_wrong_count(self):
        from app.services.vision_client import VisionClient, VisionError
        client = VisionClient('/unused')
        for scores in ([float('nan')], [], ['bad']):
            client.request = Mock(return_value={'scores': scores})
            with self.assertRaises(VisionError):
                client.quality([Image.new('RGB', (2, 2))])


class ClipRoutingTests(unittest.TestCase):
    def test_squash_preserves_edges_shortest_crops(self):
        from app.clip_onnx import ClipEncoder
        encoder = ClipEncoder.__new__(ClipEncoder)
        encoder._image_size = 4
        encoder._resample = Image.Resampling.NEAREST
        encoder._mean = np.zeros((3, 1, 1)); encoder._std = np.ones((3, 1, 1))
        arr = np.zeros((4, 12, 3), dtype=np.uint8); arr[:, :3] = 255
        encoder._resize_mode = 'squash'
        self.assertGreater(encoder.preprocess(Image.fromarray(arr))[0, 0, 0], .9)
        encoder._resize_mode = 'shortest'
        self.assertEqual(encoder.preprocess(Image.fromarray(arr)).sum(), 0)
        del encoder._resize_mode
        self.assertEqual(encoder.preprocess(Image.fromarray(arr)).sum(), 0)

    def test_matching_sidecar_avoids_loading_onnx(self):
        from app.clip_onnx import ServiceClipEncoder
        from app.config.schema import ClipModel
        model = ClipModel.SIGLIP2_SO400M
        client = Mock(); client.available.return_value = True
        client.embed_texts.return_value = np.ones((1, 1152))
        factory = Mock()
        encoder = ServiceClipEncoder(model, client, factory)
        encoder.encode_texts(['hello'])
        client.embed_texts.assert_called_once_with(['hello'], model.hf_repo, 1152)
        factory.assert_not_called()

    def test_failure_or_mismatch_falls_back(self):
        from app.clip_onnx import ServiceClipEncoder
        from app.config.schema import ClipModel
        from app.services.vision_client import VisionError
        client = Mock(); client.available.return_value = True
        client.embed_images.side_effect = VisionError('failed')
        fallback = Mock(); fallback.encode_images.return_value = np.array([[1., 0.]])
        encoder = ServiceClipEncoder(ClipModel.VIT_B_32, client, lambda: fallback)
        images = [Image.new('RGB', (2, 2))]
        np.testing.assert_equal(encoder.encode_images(images), [[1., 0.]])
        client.invalidate.assert_called_once()
        client.available.return_value = False
        encoder.encode_images(images)
        self.assertEqual(fallback.encode_images.call_count, 2)

    def test_extractor_uses_raw_image_route(self):
        from app.processors.embedding_extractor import EmbeddingExtractor
        processor = EmbeddingExtractor(); processor._clip = Mock()
        processor._clip.encode_images.return_value = np.array([[1., 0.]])
        images = [Image.new('RGB', (2, 2))]
        np.testing.assert_equal(processor._get_embeddings_batch(images)[0], [1., 0.])
        processor._clip.encode_images.assert_called_once_with(images)


class QualityTests(unittest.TestCase):
    def test_photo_original_downscaled(self):
        from app.processors.quality import QualityProcessor
        from app.models import Media
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.jpg'; Image.new('RGB', (2048, 512)).save(path)
            media = Media(id=1, path=str(path), filename='photo.jpg', size=1)
            processor = QualityProcessor(); client = Mock(); client.quality.return_value = [.7]
            with patch('app.processors.quality.get_vision_client', return_value=client), patch('app.processors.quality.safe_commit'):
                self.assertTrue(processor.process(media, Mock(), []))
            image = client.quality.call_args.args[0][0]
            self.assertEqual(image.size, (1024, 256))
            self.assertEqual(media.quality_score, .7)

    def test_video_top_three_and_unavailable_pending(self):
        from app.processors.quality import QualityProcessor
        from app.models import Media
        from app.services.vision_client import VisionError
        media = Media(id=1, path='video', filename='v', size=1, duration=10)
        processor = QualityProcessor(); client = Mock(); client.quality.return_value = [.1, .9, .8, .7]
        scenes = [(None, np.zeros((2, 2, 3), dtype=np.uint8))] * 4
        with patch('app.processors.quality.get_vision_client', return_value=client), patch('app.processors.quality.safe_commit'):
            self.assertTrue(processor.process(media, Mock(), scenes))
            self.assertAlmostEqual(media.quality_score, .8)
            processor.reset_for_media(media, Mock())
            client.quality.side_effect = VisionError('offline')
            self.assertTrue(processor.process(media, Mock(), scenes))
            self.assertIsNone(media.quality_score)
        self.assertIn('IS NULL', str(processor.get_pending_condition()))

    def test_registration_and_settings(self):
        from app.config.schema import AppSettings, ClipModel
        from app.processors.quality import QualityProcessor
        settings = AppSettings()
        self.assertTrue(settings.vision.enabled)
        self.assertTrue(settings.processors.quality_processor_active)
        self.assertEqual(ClipModel.SIGLIP2_SO400M.embedding_size, 1152)
        self.assertGreater(QualityProcessor.order, 5)
        self.assertLess(QualityProcessor.order, 10)
        from app.processor_registry import load_processors
        self.assertIn('quality', [p.name for p in load_processors()])


class RebuildTests(unittest.TestCase):
    def setUp(self):
        from app.database import _attach_engine_listeners, ensure_vec_tables
        from app.config.schema import ClipModel
        from app.models import Media, ProcessingTask
        self.engine = create_engine('sqlite://')
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        ensure_vec_tables(target_engine=self.engine, clip_model=ClipModel.VIT_B_32)
        with Session(self.engine) as session:
            session.add(Media(id=1, path='a', filename='a', size=1, embeddings_created=True))
            session.commit()
        with self.engine.begin() as conn:
            conn.exec_driver_sql('INSERT INTO media_embeddings(media_id, embedding) VALUES (?, ?)', (1, np.ones(512, dtype='<f4').tobytes()))

    def tearDown(self):
        self.engine.dispose()

    def test_rebuild_dimension_flags_and_idempotence(self):
        from scripts.rebuild_clip_vectors import rebuild_clip_vectors
        from app.config.schema import ClipModel
        from app.models import Media
        for _ in range(2):
            rebuild_clip_vectors(target_engine=self.engine, model=ClipModel.SIGLIP2_SO400M)
            with self.engine.connect() as conn:
                self.assertIn('1152', conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='scene_embeddings'").scalar())
                self.assertEqual(conn.exec_driver_sql('SELECT count(*) FROM media_embeddings').scalar(), 0)
            with Session(self.engine) as session:
                self.assertFalse(session.get(Media, 1).embeddings_created)

    def test_running_task_refuses_without_changes(self):
        from scripts.rebuild_clip_vectors import rebuild_clip_vectors
        from app.models import ProcessingTask, Status
        for kind in ('process_media', 'run_processor'):
            with Session(self.engine) as session:
                task = ProcessingTask(task_type=kind, status=Status.RUNNING); session.add(task); session.commit()
                with self.assertRaises(RuntimeError):
                    rebuild_clip_vectors(target_engine=self.engine)
                session.delete(task); session.commit()
            with self.engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql('SELECT count(*) FROM media_embeddings').scalar(), 1)

    def test_model_config_write_preserves_other_keys(self):
        import yaml
        from scripts.rebuild_clip_vectors import rebuild_clip_vectors
        from app.config.schema import ClipModel
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'; config.write_text('ai:\n  min_search_dist: 0.8\ngeneral:\n  port: 8123\n')
            rebuild_clip_vectors(target_engine=self.engine, model=ClipModel.SIGLIP2_SO400M, config_path=config)
            data = yaml.safe_load(config.read_text())
            self.assertEqual(data['ai']['clip_model'], ClipModel.SIGLIP2_SO400M.model_name)
            self.assertEqual(data['ai']['min_search_dist'], .8)
            self.assertEqual(data['general']['port'], 8123)

class PipelineQualityTests(unittest.TestCase):
    def test_pending_quality_does_not_block_later_processors(self):
        from app.models import Media
        from app.processors.quality import QualityProcessor
        from app.tasks import media_processing as processing
        engine = create_engine('sqlite://'); self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            media = Media(path='missing.jpg', filename='m', size=1)
            session.add(media); session.commit()
            quality = QualityProcessor(); quality.active = True
            later = SimpleNamespace(name='later', active=True, process=Mock(return_value=True))
            with patch.object(processing, 'processors', [quality, later]):
                self.assertTrue(processing._apply_processors(media, [object()], session))
            later.process.assert_called_once()
            self.assertIsNone(media.quality_score)
            self.assertIsNone(media.processing_error)

    def test_automatic_pending_filter_and_edit_names(self):
        from app.tasks import media_processing as processing
        from app.config import settings
        with patch.object(processing, 'processors', [SimpleNamespace(name='quality', active=True)]):
            self.assertIn('media.quality_score IS NULL', str(processing._media_processing_conditions()[0]))
        with patch.object(settings.processors, 'quality_processor_active', True):
            self.assertIn('quality', processing.edit_processor_names())
        with patch.object(settings.processors, 'quality_processor_active', False):
            self.assertNotIn('quality', processing.edit_processor_names())


class RebuildFailureTests(unittest.TestCase):
    setUp = RebuildTests.setUp
    tearDown = RebuildTests.tearDown
    def test_ddl_failure_rolls_back_old_vectors_and_config(self):
        from scripts.rebuild_clip_vectors import rebuild_clip_vectors
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'; config.write_text('ai: {}\n')
            with patch('app.database.create_vec_tables', side_effect=RuntimeError('injected')):
                with self.assertRaises(RuntimeError):
                    rebuild_clip_vectors(target_engine=self.engine, config_path=config)
            self.assertEqual(config.read_text(), 'ai: {}\n')
            with self.engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql('SELECT count(*) FROM media_embeddings').scalar(), 1)

class MigrationTests(unittest.TestCase):
    def test_quality_migration_preserves_rows_and_creates_index(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/f1a2b3c4d5e6_media_quality_score.py'
        spec = importlib.util.spec_from_file_location('quality_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        engine = create_engine('sqlite://'); self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE media (id INTEGER PRIMARY KEY)')
            conn.exec_driver_sql('INSERT INTO media VALUES (1)')
            with patch.object(migration, 'op', Operations(MigrationContext.configure(conn))):
                migration.upgrade()
                self.assertIsNone(conn.exec_driver_sql('SELECT quality_score FROM media WHERE id=1').scalar())
                self.assertIn('ix_media_quality_score', [index['name'] for index in inspect(conn).get_indexes('media')])
                migration.downgrade()
                self.assertEqual(conn.exec_driver_sql('SELECT id FROM media').scalar(), 1)


class ClientTransportTests(unittest.TestCase):
    def test_transport_sets_timeout_protocol_and_closes_socket(self):
        from app.services import vision_client as module
        response = json.dumps({'ok': True, 'protocol': module.PROTOCOL_VERSION}).encode()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [struct.pack('!I', len(response)), response]
        with patch.object(module.socket, 'socket', return_value=connection):
            module.VisionClient('/test.sock', timeout=12).request('health')
        connection.settimeout.assert_called_once_with(12)
        connection.connect.assert_called_once_with('/test.sock')
        sent = json.loads(connection.sendall.call_args.args[0][4:])
        self.assertEqual(sent, {'protocol': module.PROTOCOL_VERSION, 'action': 'health'})
        connection.__exit__.assert_called_once()


class ClientByteBatchingTests(unittest.TestCase):
    def test_image_requests_split_by_encoded_size(self):
        # Full-resolution photos as lossless PNG are several MB each; batching
        # by count alone exceeded the 64 MiB message cap at 64 items.
        import app.services.vision_client as vc
        client = vc.VisionClient('/nonexistent.sock')
        sizes = []

        def fake_request(action, **payload):
            items = payload['images']
            sizes.append(sum(len(i['data']) for i in items))
            vectors = [base64.b64encode(np.eye(4, dtype='<f4')[0].tobytes()).decode()] * len(items)
            return {'ok': True, 'model_id': 'm', 'vectors': vectors}

        noise = np.random.default_rng(0).integers(0, 256, (600, 800, 3), dtype=np.uint8)
        images = [Image.fromarray(noise)] * 10
        with patch.object(vc, 'MAX_BATCH_BYTES', 3_000_000), patch.object(client, 'request', side_effect=fake_request):
            out = client.embed_images(images, 'm', 4)
        self.assertEqual(out.shape, (10, 4))
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(size <= 3_000_000 for size in sizes), sizes)

    def test_quality_requests_split_by_encoded_size(self):
        import app.services.vision_client as vc
        client = vc.VisionClient('/nonexistent.sock')
        sizes = []

        def fake_request(action, **payload):
            sizes.append(sum(len(i['data']) for i in payload['images']))
            return {'ok': True, 'scores': [0.5] * len(payload['images'])}

        noise = np.random.default_rng(1).integers(0, 256, (600, 800, 3), dtype=np.uint8)
        with patch.object(vc, 'MAX_BATCH_BYTES', 3_000_000), patch.object(client, 'request', side_effect=fake_request):
            scores = client.quality([Image.fromarray(noise)] * 10)
        self.assertEqual(len(scores), 10)
        self.assertTrue(all(size <= 3_000_000 for size in sizes), sizes)


class RawTransportTests(unittest.TestCase):
    def test_client_sends_lossless_raw_rgb(self):
        import app.services.vision_client as vc
        pixels = np.random.default_rng(2).integers(0, 256, (6, 9, 3), dtype=np.uint8)
        item = vc.encode_image(Image.fromarray(pixels))
        self.assertEqual((item['format'], item['width'], item['height']), ('rgb24', 9, 6))
        np.testing.assert_array_equal(
            np.frombuffer(base64.b64decode(item['data']), dtype=np.uint8).reshape(6, 9, 3), pixels)
