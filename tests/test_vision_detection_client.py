"""Validate new worker responses without a socket, GPU, or model weights."""
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from app.services.vision_client import VisionClient, VisionError, encode_image, PROTOCOL_VERSION


class VisionDetectionClientTests(unittest.TestCase):
    def setUp(self):
        self.client = VisionClient('/unused')
        self.image = Image.new('RGB', (9, 7), 'red')

    def test_person_response_and_invalid_results(self):
        valid = {'score': .8, 'boxes': [[1., 2., 3., 4., .8]]}
        self.client.request = Mock(return_value={'results': [valid]})
        self.assertEqual(self.client.detect_persons([self.image]), [valid])
        self.client.request.assert_called_once_with('detect_persons', images=[encode_image(self.image)])
        for bad in (None, [], [{}], [{'score': float('nan'), 'boxes': []}],
                    [{'score': 1., 'boxes': [[0, 0, 1, 2, float('inf')]]}],
                    [{'score': 1., 'boxes': [[0, 0, -1, 2, .8]]}]):
            self.client.request.return_value = {'results': bad}
            with self.assertRaises(VisionError):
                self.client.detect_persons([self.image])

    def test_text_response_and_invalid_quads(self):
        result = {'regions': [{'text': 'site.com', 'quad': [0, 0, 5, 0, 5, 2, 0, 2]}]}
        self.client.request = Mock(return_value={'results': [result]})
        self.assertEqual(self.client.detect_text([self.image]), [result])
        for quad in ([1], [float('nan')] * 8, None):
            self.client.request.return_value = {'results': [{'regions': [{'text': 'x', 'quad': quad}]}]}
            with self.assertRaises(VisionError):
                self.client.detect_text([self.image])

    def test_inpaint_rejects_outside_mask_mutation_and_wrong_size(self):
        mask = Image.new('L', self.image.size); mask.putpixel((2, 3), 255)
        cleaned = self.image.copy(); cleaned.putpixel((2, 3), (1, 2, 3))
        self.client.request = Mock(return_value={'image': encode_image(cleaned)})
        np.testing.assert_array_equal(np.asarray(self.client.inpaint(self.image, mask)), np.asarray(cleaned))
        cleaned.putpixel((0, 0), (0, 0, 0))
        self.client.request.return_value = {'image': encode_image(cleaned)}
        with self.assertRaises(VisionError):
            self.client.inpaint(self.image, mask)
        with self.assertRaises(VisionError):
            self.client.inpaint(self.image, Image.new('L', (1, 1)))

    def test_empty_detection_and_old_protocol_availability(self):
        self.client.request = Mock(return_value={'protocol': 'omoide-vision/v1', 'model_id': 'x', 'embed_dim': 2})
        self.assertFalse(self.client.available('x', 2))
        self.assertEqual(PROTOCOL_VERSION, 'omoide-vision/v2')
        self.assertEqual(self.client.detect_persons([]), [])
        self.assertEqual(self.client.detect_text([]), [])
