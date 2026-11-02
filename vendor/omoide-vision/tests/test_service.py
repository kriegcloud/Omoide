import base64
import io
import json
from pathlib import Path
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ServiceTests(unittest.TestCase):
    def test_framing_roundtrip(self):
        from omoide_vision.service import read_message, write_message
        connection = Mock()
        write_message(connection, {'action': 'health'})
        raw = connection.sendall.call_args.args[0]
        connection.recv.side_effect = [raw[:4], raw[4:]]
        self.assertEqual(read_message(connection), {'action': 'health'})

    def test_framing_limits(self):
        from omoide_vision.service import read_message, WorkerError, MAX_REQUEST_BYTES
        for chunks in ([struct.pack('!I', MAX_REQUEST_BYTES + 1)], [b''], [struct.pack('!I', 2), b'[]']):
            connection = Mock(); connection.recv.side_effect = chunks
            with self.assertRaises(WorkerError):
                read_message(connection)

    def test_request_validation(self):
        from omoide_vision.service import VisionService, WorkerError, PROTOCOL_VERSION
        service = VisionService.__new__(VisionService)
        for payload in ({}, {'action': 'bogus'}, {'action': 'embed_images', 'images': []},
                        {'action': 'embed_images', 'images': ['bad'] * 65},
                        {'action': 'embed_texts', 'texts': [1]},
                        {'action': 'embed_images', 'images': ['not base64']}):
            with self.assertRaises(WorkerError):
                service.handle({'protocol': PROTOCOL_VERSION, **payload})
        with self.assertRaises(WorkerError):
            service.handle({'protocol': 'wrong', 'action': 'health'})

    def test_vector_encoding(self):
        from omoide_vision.service import encode_vectors, WorkerError
        encoded = encode_vectors(np.array([[3., 4.]], dtype=np.float16))
        value = np.frombuffer(base64.b64decode(encoded[0]), dtype='<f4')
        np.testing.assert_allclose(value, [.6, .8])
        self.assertEqual(len(base64.b64decode(encoded[0])), 8)
        for invalid in (np.array([[0., 0.]]), np.array([[float('nan'), 1.]])):
            with self.assertRaises(WorkerError):
                encode_vectors(invalid)

    def test_decode_exif_rgb(self):
        from omoide_vision.service import decode_image
        source = Image.new('RGB', (6, 2))
        exif = source.getexif(); exif[274] = 6
        buffer = io.BytesIO(); source.save(buffer, format='JPEG', exif=exif)
        image = decode_image(base64.b64encode(buffer.getvalue()).decode())
        self.assertEqual(image.size, (2, 6))
        self.assertEqual(image.mode, 'RGB')

    def test_model_loading_and_lazy_quality_with_stubs(self):
        from omoide_vision.service import VisionService, MODEL_NAME
        torch = MagicMock(); clip = MagicMock(); pyiqa = MagicMock()
        model = Mock(); transform = Mock()
        clip.create_model_and_transforms.return_value = (model, None, transform)
        with patch.dict(sys.modules, {'torch': torch, 'open_clip': clip, 'pyiqa': pyiqa}):
            service = VisionService(SimpleNamespace(device=0, batch_size=16))
            torch.set_num_threads.assert_called_once_with(8)
            clip.create_model_and_transforms.assert_called_once_with(MODEL_NAME, pretrained='webli', device='cuda:0', precision='fp16')
            pyiqa.create_metric.assert_not_called()
            service.score_images([Image.new('RGB', (2, 2))])
            pyiqa.create_metric.assert_called_once_with('topiq_nr', device='cuda:0')
            service.score_images([Image.new('RGB', (2, 2))])
            self.assertEqual(pyiqa.create_metric.call_count, 1)

    def test_raw_rgb24_items_decode_exactly(self):
        # PNG transport cost ~60 ms/img (encode+decode); raw RGB is ~3 ms.
        from omoide_vision.service import decode_image, WorkerError
        pixels = np.random.default_rng(0).integers(0, 256, (5, 7, 3), dtype=np.uint8)
        item = {'format': 'rgb24', 'width': 7, 'height': 5,
                'data': base64.b64encode(pixels.tobytes()).decode()}
        image = decode_image(item)
        self.assertEqual(image.size, (7, 5))
        np.testing.assert_array_equal(np.asarray(image), pixels)
        with self.assertRaises(WorkerError):
            decode_image({**item, 'width': 8})
        with self.assertRaises(WorkerError):
            decode_image({**item, 'format': 'bgr'})
        with self.assertRaises(WorkerError):
            decode_image({**item, 'width': 100000, 'height': 100000})

    def test_quality_inputs_use_fixed_koniq_buckets(self):
        # Variable shapes trigger a MIOpen kernel search per image on ROCm
        # (measured 5.9 s/img); fixed buckets measured 28.7 ms/img.
        from omoide_vision.service import quality_batches
        images = [Image.new('RGB', (4000, 3000)), Image.new('RGB', (900, 1600)),
                  Image.new('RGB', (1024, 1024)), Image.new('RGB', (300, 500))]
        batches = quality_batches(images)
        shapes = {tuple(pixels.shape[1:]): indices for indices, pixels in batches}
        self.assertEqual(shapes, {(3, 768, 1024): [0, 2], (3, 1024, 768): [1, 3]})
        for _, pixels in batches:
            self.assertEqual(pixels.dtype, np.float32)
            self.assertGreaterEqual(float(pixels.min()), 0.0)
            self.assertLessEqual(float(pixels.max()), 1.0)

    def test_health_and_responses(self):
        from omoide_vision.service import VisionService, PROTOCOL_VERSION, MODEL_ID
        service = VisionService.__new__(VisionService)
        service.device_name = 'GPU'; service.device = 'cuda:0'; service.quality_model = None
        service.embed_images = Mock(return_value=np.array([[1., 0.]]))
        service.embed_texts = Mock(return_value=np.array([[1., 0.]]))
        service.score_images = Mock(return_value=[.5])
        health = service.handle({'protocol': PROTOCOL_VERSION, 'action': 'health'})
        self.assertEqual(health['embed_dim'], 1152)
        self.assertEqual(health['model_id'], MODEL_ID)
        buffer = io.BytesIO(); Image.new('RGB', (2, 2)).save(buffer, 'PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode()
        raw = {'format': 'rgb24', 'width': 2, 'height': 2, 'data': base64.b64encode(bytes(12)).decode()}
        for action, inputs in (('embed_images', {'images': [encoded]}), ('embed_images', {'images': [raw]}),
                               ('embed_texts', {'texts': ['a']}), ('quality', {'images': [encoded]}),
                               ('quality', {'images': [raw]})):
            response = service.handle({'protocol': PROTOCOL_VERSION, 'action': action, **inputs})
            self.assertTrue(response['ok'])
            self.assertEqual(response['protocol'], PROTOCOL_VERSION)
            self.assertIn('scores' if action == 'quality' else 'vectors', response)

    def test_inference_batches_images_fp16_and_texts(self):
        from omoide_vision.service import VisionService
        service = VisionService.__new__(VisionService)
        service.torch = MagicMock(); service.model = Mock()
        service.device = 'cuda:0'; service.batch_size = 2
        service.preprocess = Mock(); service.tokenizer = Mock()
        for name in ('encode_image', 'encode_text'):
            getattr(service.model, name).return_value.float.return_value.cpu.return_value.numpy.side_effect = [np.ones((2, 3)), np.ones((1, 3))]
        images = [Image.new('RGB', (2, 2))] * 3
        self.assertEqual(service.embed_images(images).shape, (3, 3))
        self.assertEqual(service.model.encode_image.call_count, 2)
        service.torch.stack.return_value.to.assert_called_with(device='cuda:0', dtype=service.torch.float16)
        self.assertEqual(service.embed_texts(['a', 'b', 'c']).shape, (3, 3))
        self.assertEqual(service.tokenizer.call_args_list[0].args[0], ['a', 'b'])
        self.assertEqual(service.tokenizer.call_args_list[1].args[0], ['c'])
