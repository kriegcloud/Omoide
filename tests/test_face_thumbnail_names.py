"""Face crop thumbnails must fit the 255-byte filename limit.

Names were ``<source stem>_ins_<i>_<ms>.jpg``; a press-photo file with a
~240-character name raised ``OSError: [Errno 36] File name too long`` in the
faces processor, failing the media and the whole process_media task
(2026-11-02, 25 media).
"""

import unittest

from app.processors.faces import face_thumbnail_name

NAME_MAX = 255


class FaceThumbnailNameTests(unittest.TestCase):
    def test_short_names_keep_the_existing_format(self):
        self.assertEqual(
            face_thumbnail_name("/m/holiday.jpg", 2, 1793603853634),
            "holiday_ins_2_1793603853634.jpg",
        )

    def test_long_ascii_stem_is_bounded(self):
        stem = "los-angeles-" * 25  # 300 chars
        name = face_thumbnail_name(f"/m/{stem}.jpg", 0, 1793603853634)
        self.assertLessEqual(len(name.encode()), NAME_MAX)
        self.assertTrue(name.endswith("_ins_0_1793603853634.jpg"))
        self.assertTrue(name.startswith("los-angeles-"))

    def test_long_multibyte_stem_is_bounded_and_valid_utf8(self):
        stem = "思い出" * 60  # 540 bytes
        name = face_thumbnail_name(f"/m/{stem}.png", 12, 1)
        encoded = name.encode()
        self.assertLessEqual(len(encoded), NAME_MAX)
        self.assertEqual(encoded.decode(), name)

    def test_distinct_long_stems_with_shared_prefix_stay_distinct(self):
        prefix = "x" * 300
        a = face_thumbnail_name(f"/m/{prefix}-a.jpg", 0, 5)
        b = face_thumbnail_name(f"/m/{prefix}-b.jpg", 0, 5)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
