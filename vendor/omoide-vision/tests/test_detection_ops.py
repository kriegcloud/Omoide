"""CPU-only protocol/model contracts; no model weights or GPU are used."""
import base64
import io
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omoide_vision.service import (VisionService, WorkerError, PROTOCOL_VERSION, PERSON_MODEL_ID,
                                  TEXT_MODEL_ID, FLORENCE_TASK, encode_image, decode_mask,
                                  composite, repair)


class DetectionOpsTests(unittest.TestCase):
    def service(self):
        service = VisionService.__new__(VisionService)
        service.torch = MagicMock()
        service.device = 'cuda:1'
        service.batch_size = 2
        service.person_model = service.person_processor = None
        service.text_model = service.text_processor = None
        service.lama_model = None
        return service

    def test_person_lazy_offline_fixed_shape_and_xywh(self):
        service = self.service()
        transformers = MagicMock()
        processor = transformers.AutoImageProcessor.from_pretrained.return_value
        model = transformers.AutoModelForObjectDetection.from_pretrained.return_value.to.return_value.eval.return_value
        model.config.id2label = {0: 'person', 1: 'cat'}
        def tensor(values):
            value = Mock(); value.tolist.return_value = values; return value
        detection = {'scores': tensor([.34, .8, .99]), 'labels': tensor([0, 0, 1]),
                     'boxes': tensor([[0, 0, 1, 1], [-1, 2, 8, 7], [0, 0, 1, 1]])}
        processor.post_process_object_detection.return_value = [detection]
        with patch.dict(sys.modules, {'transformers': transformers}):
            result = service.detect_persons([Image.new('RGB', (10, 10))])
            service.detect_persons([Image.new('RGB', (10, 10))])
        self.assertEqual(result, [{'score': .8, 'boxes': [[0., 2, 8., 5, .8]]}])
        transformers.AutoModelForObjectDetection.from_pretrained.assert_called_once_with(
            PERSON_MODEL_ID, local_files_only=True, torch_dtype=service.torch.float16)
        self.assertEqual(processor.call_args.kwargs['size'], {'height': 640, 'width': 640})
        self.assertEqual(processor.post_process_object_detection.call_args.kwargs['threshold'], 0.)

    def test_text_lazy_offline_generation_and_original_coordinates(self):
        service = self.service()
        transformers = MagicMock()
        processor = transformers.AutoProcessor.from_pretrained.return_value
        model = transformers.Florence2ForConditionalGeneration.from_pretrained.return_value.to.return_value.eval.return_value
        processor.batch_decode.return_value = ['generated']
        quad = [1., 2., 10., 2., 10., 4., 1., 4.]
        processor.post_process_generation.return_value = {FLORENCE_TASK: {'labels': ['handle.com'], 'quad_boxes': [quad]}}
        with patch.dict(sys.modules, {'transformers': transformers}):
            result = service.detect_text([Image.new('RGB', (120, 80))])
            service.detect_text([Image.new('RGB', (120, 80))])
        self.assertEqual(result, [{'regions': [{'text': 'handle.com', 'quad': quad}]}])
        transformers.Florence2ForConditionalGeneration.from_pretrained.assert_called_once_with(
            TEXT_MODEL_ID, local_files_only=True, torch_dtype=service.torch.float16)
        self.assertEqual(processor.call_args.kwargs['images'].size, (768, 768))
        self.assertEqual(processor.post_process_generation.call_args.kwargs['image_size'], (120, 80))
        self.assertEqual(model.generate.call_args.kwargs['num_beams'], 3)
        self.assertEqual(model.generate.call_args.kwargs['max_new_tokens'], 256)

    def test_wire_ops_and_mask_validation(self):
        service = self.service()
        image = Image.new('RGB', (3, 5), 'red')
        mask = Image.new('L', image.size, 255)
        stream = io.BytesIO(); mask.save(stream, 'PNG')
        payload = base64.b64encode(stream.getvalue()).decode()
        self.assertEqual(decode_mask(payload, image.size).size, image.size)
        with self.assertRaises(WorkerError):
            decode_mask(payload, (5, 3))
        for op, result in [('detect_persons', [{'score': 0., 'boxes': []}]),
                           ('detect_text', [{'regions': []}])]:
            setattr(service, op, Mock(return_value=result))
            reply = service.handle({'protocol': PROTOCOL_VERSION, 'action': op, 'images': [encode_image(image)]})
            self.assertEqual(reply['results'], result)
        service.inpaint = Mock(return_value=image)
        reply = service.handle({'protocol': PROTOCOL_VERSION, 'action': 'inpaint',
                                'image': encode_image(image), 'mask': payload})
        self.assertEqual(reply['image'], encode_image(image))

    def test_hard_composite_preserves_each_outside_byte(self):
        source = np.random.default_rng(8).integers(0, 256, (11, 17, 3), dtype=np.uint8)
        generated = np.full_like(source, 32)
        mask = np.zeros((11, 17), dtype=np.uint8); mask[3:6, 4:8] = 1
        output = composite(source, generated, mask)
        np.testing.assert_array_equal(output[mask == 0], source[mask == 0])
        np.testing.assert_array_equal(output[mask > 0], generated[mask > 0])

    def test_repair_crop_padding_and_empty_mask_without_gpu(self):
        import torch
        image = Image.fromarray(np.random.default_rng(9).integers(0, 256, (35, 41, 3), dtype=np.uint8))
        mask = Image.new('L', image.size); mask.putpixel((20, 18), 255)
        def fake(rgb, mt):
            self.assertEqual(tuple(rgb.shape), (1, 3, 8, 8))
            self.assertEqual(tuple(mt.shape), (1, 1, 8, 8))
            return torch.zeros_like(rgb)
        result = repair(image, mask, fake, torch, 'cpu', context=2)
        outside = np.asarray(mask) == 0
        np.testing.assert_array_equal(np.asarray(result)[outside], np.asarray(image)[outside])
        np.testing.assert_array_equal(np.asarray(result)[18, 20], [0, 0, 0])
        unused = Mock()
        empty = repair(image, Image.new('L', image.size), unused, torch, 'cpu')
        self.assertEqual(empty.tobytes(), image.tobytes()); unused.assert_not_called()
