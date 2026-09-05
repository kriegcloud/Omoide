import unittest

from PIL import Image

from app.image_limits import apply_pillow_limits


class PillowLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = Image.MAX_IMAGE_PIXELS

    def tearDown(self) -> None:
        Image.MAX_IMAGE_PIXELS = self._previous

    def test_positive_value_is_applied(self) -> None:
        self.assertEqual(apply_pillow_limits(400_000_000), 400_000_000)
        self.assertEqual(Image.MAX_IMAGE_PIXELS, 400_000_000)

    def test_non_positive_disables_the_guard(self) -> None:
        self.assertIsNone(apply_pillow_limits(0))
        self.assertIsNone(Image.MAX_IMAGE_PIXELS)
        self.assertIsNone(apply_pillow_limits(None))

    def test_200_megapixel_phone_photo_fits_default(self) -> None:
        from app.config import ScanSettings

        limit = ScanSettings().max_image_pixels
        s24_ultra = 16320 * 12240  # 199.8 MP, decoded as 122922240 in-app once demosaiced/rotated
        self.assertGreater(limit, s24_ultra)
        self.assertGreater(2 * limit, 142_851_072)  # largest skipped file seen


if __name__ == "__main__":
    unittest.main()
