"""Regression coverage for the pipeline-bugs lane (no HTTP clients)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import imagehash
import numpy as np
from PIL import Image
from sqlmodel import select
from round6_support import DatabaseCase
from app.models import DuplicateMedia, ProcessingTask
from app.phash import generate_perceptual_hash
from app.processors.duplicates import DuplicateProcessor


class ImageDuplicateTests(DatabaseCase):
    def test_resaved_downscaled_images_group(self):
        rng = np.random.default_rng(42)
        pixels = rng.integers(0, 256, (256, 320, 3), dtype=np.uint8)
        original = Image.fromarray(pixels)
        a, b = self.root / 'a.png', self.root / 'b.jpg'
        original.save(a)
        original.resize((160, 128)).save(b, quality=85)
        first, second = self.media(path=str(a)), self.media(path=str(b))
        for media in (first, second):
            media.phash = generate_perceptual_hash(media, 'image')
        distance = imagehash.hex_to_hash(first.phash) - imagehash.hex_to_hash(second.phash)
        self.assertGreaterEqual(distance, 1)
        self.assertLessEqual(distance, 4)
        self.session.add_all([first, second])
        task = ProcessingTask(id='near-images', task_type='find_duplicates', status='pending')
        self.session.add(task)
        self.session.commit()
        with patch('app.processors.duplicates.db.engine', self.engine):
            DuplicateProcessor(task.id, 4).process()
        links = self.session.exec(select(DuplicateMedia)).all()
        self.assertEqual({link.media_id for link in links}, {first.id, second.id})
        self.assertEqual(len({link.group_id for link in links}), 1)

    def test_exif_orientation_matches_rotated_pixels(self):
        pixels = np.random.default_rng(4).integers(0, 256, (90, 130, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
        exif = image.getexif()
        exif[274] = 6
        a, b = self.root / 'exif.png', self.root / 'pixels.png'
        image.save(a, exif=exif)
        image.transpose(Image.Transpose.ROTATE_270).save(b)
        self.assertEqual(generate_perceptual_hash(self.media(path=str(a)), 'image'),
                         generate_perceptual_hash(self.media(path=str(b)), 'image'))

    def test_banded_pairs_match_brute_force(self):
        from app.processors.duplicates import image_hash_pairs
        rng = np.random.default_rng(9)
        values = [int(x) for x in rng.integers(0, 2**64, 500, dtype=np.uint64)]
        for i in range(0, 80, 2):
            values[i + 1] = values[i] ^ sum(1 << int(b) for b in rng.choice(64, 4, replace=False))
        for threshold in (0, 1, 4, 8):
            expected = {(i, j) for i in range(500) for j in range(i + 1, 500)
                        if (values[i] ^ values[j]).bit_count() <= threshold}
            self.assertEqual(set(image_hash_pairs(np.array(values, dtype=np.uint64), threshold)), expected)

    def test_ignored_images_cannot_join_through_near_copy(self):
        from app.models import DuplicateIgnore
        first, second, bridge = [self.media(phash=value) for value in (
            "0000000000000000", "0000000000000003", "0000000000000001")]
        self.session.add(DuplicateIgnore(media_id_a=first.id, media_id_b=second.id))
        task = ProcessingTask(id='ignored-images', task_type='find_duplicates', status='pending')
        self.session.add(task)
        self.session.commit()
        with patch('app.processors.duplicates.db.engine', self.engine):
            DuplicateProcessor(task.id, 1).process()
        groups = {}
        for link in self.session.exec(select(DuplicateMedia)).all():
            groups.setdefault(link.group_id, set()).add(link.media_id)
        self.assertTrue(groups)
        self.assertTrue(all(not {first.id, second.id}.issubset(ids) for ids in groups.values()))

    def test_rehash_updates_existing_images_and_leaves_videos(self):
        from app.tasks.hashes import rehash_images
        image = self.media(phash='old-image')
        video = self.media(duration=1, phash='old-video')
        with patch('app.tasks.hashes.generate_perceptual_hash', return_value='new-image') as generate:
            self.assertEqual(rehash_images(self.session), 1)
        self.session.refresh(image)
        self.session.refresh(video)
        self.assertEqual(image.phash, 'new-image')
        self.assertEqual(video.phash, 'old-video')
        self.assertEqual(generate.call_count, 1)


class BlurTests(DatabaseCase):
    def test_processor_and_task_use_original_identically(self):
        from app.frames import load_photo_scene
        from app.processors.blur import BlurProcessor
        from app.tasks.blur import _score_media
        path, thumb = self.root / 'original.jpg', self.root / 'thumb.jpg'
        Image.fromarray(np.random.default_rng(2).integers(0, 256, (1300, 1600, 3), dtype=np.uint8)).save(path)
        Image.new('RGB', (80, 80)).save(thumb)
        media = self.media(path=str(path), thumbnail_path=thumb.name)
        scene = load_photo_scene(path)
        self.addCleanup(scene.image.close)
        BlurProcessor().process(media, self.session, [scene])
        self.assertAlmostEqual(media.laplacian_score, _score_media(self.root, thumb.name, str(path)), delta=1e-6)


class FrameTests(DatabaseCase):
    def test_sharpest_per_window_and_uniform_count(self):
        from app.frames import _split_by_frames
        media = self.media(duration=10)
        Path(media.path).touch()
        def extract(_media, timestamps, duration):
            from app.models import Scene
            return [(Scene(media_id=media.id, start_time=t, end_time=t, thumbnail_path=str(i)),
                     np.full((8, 8, 3), [1, 9, 3][i % 3], dtype=np.uint8))
                    for i, t in enumerate(timestamps)]
        with patch('app.frames._extract_frames_at', side_effect=extract), patch('app.sharpness.laplacian_score', side_effect=lambda arr: float(arr.mean())):
            result = _split_by_frames(media)
        self.assertEqual(len(result), 5)
        self.assertEqual([int(frame.mean()) for _, frame in result], [9] * 5)
        self.assertEqual([scene.start_time for scene, _ in result], [0, 2, 4, 6, 8])
        self.assertEqual([scene.end_time for scene, _ in result], [2, 4, 6, 8, 10])

    def test_discarded_candidate_thumbnails_are_removed(self):
        import tempfile
        from app.config import settings
        from app.frames import _split_by_frames
        media = self.media(duration=10)
        Path(media.path).touch()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def extract(_media, timestamps, duration):
                from app.models import Scene
                out = []
                for i, t in enumerate(timestamps):
                    (root / f"{i}.jpg").write_bytes(b"x")
                    out.append((Scene(media_id=media.id, start_time=t, end_time=t, thumbnail_path=f"{i}.jpg"),
                                np.full((8, 8, 3), [1, 9, 3][i % 3], dtype=np.uint8)))
                return out
            with patch('app.frames._extract_frames_at', side_effect=extract), \
                    patch('app.sharpness.laplacian_score', side_effect=lambda arr: float(arr.mean())), \
                    patch.object(type(settings.general), 'thumb_dir', property(lambda _self: root)):
                result = _split_by_frames(media)
            kept = sorted(scene.thumbnail_path for scene, _ in result)
            self.assertEqual(sorted(p.name for p in root.iterdir()), kept)


class ThreadTests(unittest.TestCase):
    def test_onnx_thread_limits_and_gpu_priority(self):
        from app.accelerators import create_onnx_session, resolve_onnx_providers
        ort = MagicMock()
        ort.get_available_providers.return_value = ['ROCMExecutionProvider', 'MIGraphXExecutionProvider']
        with patch.dict(sys.modules, {'onnxruntime': ort}):
            resolve_onnx_providers.cache_clear()
            self.assertNotIn('ROCMExecutionProvider', resolve_onnx_providers(True)[0])
            for value, expected in ((None, 8), ('3', 3), ('invalid', 8), ('0', 8)):
                with patch.dict(os.environ):
                    os.environ.pop('OMOIDE_ONNX_THREADS', None)
                    if value is not None:
                        os.environ['OMOIDE_ONNX_THREADS'] = value
                    create_onnx_session(Path('model.onnx'), False)
                    options = ort.InferenceSession.call_args.kwargs['sess_options']
                    self.assertEqual(options.intra_op_num_threads, expected)
                    self.assertEqual(options.inter_op_num_threads, 1)
                    options.add_session_config_entry.assert_called_with('session.intra_op.allow_spinning', '0')
            resolve_onnx_providers.cache_clear()

    def test_onnx_cpu_fallback_retains_thread_options(self):
        from app.accelerators import create_onnx_session
        ort = MagicMock()
        ort.InferenceSession.side_effect = [RuntimeError('GPU unavailable'), MagicMock()]
        with patch.dict(sys.modules, {'onnxruntime': ort}), patch(
            'app.accelerators.resolve_onnx_providers',
            return_value=(['MIGraphXExecutionProvider', 'CPUExecutionProvider'], True),
        ):
            create_onnx_session(Path('model.onnx'), True)
        first, fallback = ort.InferenceSession.call_args_list
        self.assertIs(first.kwargs['sess_options'], fallback.kwargs['sess_options'])
        self.assertEqual(fallback.kwargs['providers'], ['CPUExecutionProvider'])
