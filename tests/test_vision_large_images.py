"""Large library photos must fit the 64 MiB sidecar frame (watermark task failed on >16 MP)."""
import base64
import json
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from app.services import vision_client as vc
from app.services.vision_client import VisionClient


def _payload_bytes(call):
    action, kwargs = call.args[0], call.kwargs
    return len(json.dumps({'action': action, **kwargs}, separators=(',', ':')).encode())


class LargeImageTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = VisionClient('/unused')
        # 24 MP: raw rgb24 is 72 MB, base64 96 MB — over MAX_REQUEST_BYTES.
        self.big = Image.new('RGB', (6000, 4000), (120, 130, 140))

    def test_detection_frames_are_downscaled_and_boxes_return_in_source_pixels(self):
        def fake(action, images):
            sent = images[0]
            self.assertLessEqual(max(sent['width'], sent['height']), vc.DETECTION_MAX_EDGE)
            # A box covering the right half of what the worker saw.
            w, h = sent['width'], sent['height']
            return {'results': [{'score': .9, 'boxes': [[w / 2, 0, w / 2, h, .9]]}]}
        self.client.request = Mock(side_effect=fake)
        result = self.client.detect_persons([self.big])[0]
        self.assertLessEqual(_payload_bytes(self.client.request.call_args), vc.MAX_REQUEST_BYTES)
        x, y, w, h, score = result['boxes'][0]
        self.assertAlmostEqual(x, 3000, delta=2)
        self.assertAlmostEqual(w, 3000, delta=2)
        self.assertAlmostEqual(h, 4000, delta=2)

    def test_text_quads_return_in_source_pixels(self):
        def fake(action, images):
            w, h = images[0]['width'], images[0]['height']
            return {'results': [{'regions': [{'text': 'site.com', 'quad': [w - 10, h - 5, w, h - 5, w, h, w - 10, h]}]}]}
        self.client.request = Mock(side_effect=fake)
        quad = self.client.detect_text([self.big])[0]['regions'][0]['quad']
        self.assertAlmostEqual(quad[2], 6000, delta=2)
        self.assertAlmostEqual(quad[5], 4000, delta=2)

    def test_small_images_are_sent_unchanged(self):
        small = Image.new('RGB', (9, 7), 'red')
        self.client.request = Mock(return_value={'results': [{'regions': []}]})
        self.client.detect_text([small])
        sent = self.client.request.call_args.kwargs['images'][0]
        self.assertEqual((sent['width'], sent['height']), (9, 7))

    def test_inpaint_sends_only_the_mask_neighbourhood_and_preserves_the_rest(self):
        source = Image.fromarray(np.random.default_rng(1).integers(0, 255, (4000, 6000, 3), dtype=np.uint8))
        mask = Image.new('L', source.size)
        mask.paste(255, (5800, 3900, 5990, 3990))

        def fake(action, image, mask):
            self.assertLess(image['width'] * image['height'], 1_000_000)
            crop = Image.frombytes('RGB', (image['width'], image['height']), base64.b64decode(image['data']))
            out = np.asarray(crop).copy()
            m = np.asarray(Image.open(__import__('io').BytesIO(base64.b64decode(mask))).convert('L')) > 0
            out[m] = (0, 255, 0)
            return {'image': vc.encode_image(Image.fromarray(out))}
        self.client.request = Mock(side_effect=fake)
        cleaned = self.client.inpaint(source, mask)
        self.assertEqual(cleaned.size, source.size)
        inside = np.asarray(mask) > 0
        self.assertTrue((np.asarray(cleaned)[inside] == (0, 255, 0)).all())
        self.assertTrue(np.array_equal(np.asarray(cleaned)[~inside], np.asarray(source)[~inside]))

    def test_inpaint_with_empty_mask_returns_source_without_a_request(self):
        mask = Image.new('L', self.big.size)
        self.client.request = Mock()
        cleaned = self.client.inpaint(self.big, mask)
        self.client.request.assert_not_called()
        self.assertTrue(np.array_equal(np.asarray(cleaned), np.asarray(self.big)))


if __name__ == '__main__':
    unittest.main()
