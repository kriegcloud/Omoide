"""Read-only face gallery metadata must carry its coordinate contract."""
import unittest
from types import SimpleNamespace
from app.schemas.face import FaceRead


class FaceGallerySchemaTest(unittest.TestCase):
    def test_orm_parent_metadata_and_detector_box_are_exposed(self):
        face = SimpleNamespace(id=1, media_id=2, thumbnail_path="face.jpg", bbox=[12, 24, 36, 48], media=SimpleNamespace(width=4000, height=2000, thumbnail_path="parent.jpg", duration=None))
        result = FaceRead.model_validate(face).model_dump()
        self.assertEqual(result["bbox"], [12, 24, 36, 48])
        self.assertEqual(result["media_width"], 4000)
        self.assertEqual(result["media_height"], 2000)
        self.assertEqual(result["media_thumbnail_path"], "parent.jpg")

    def test_existing_minimal_payloads_remain_valid(self):
        result = FaceRead(id=1, media_id=2, thumbnail_path="face.jpg")
        self.assertIsNone(result.bbox)
        self.assertIsNone(result.media_thumbnail_path)

class SimilarPersonGallerySchemaTest(unittest.TestCase):
    def test_similarity_receipt_preserves_profile_geometry_and_legacy_thumbnail(self):
        from app.schemas.person import SimilarPerson
        face = FaceRead(id=3, media_id=4, thumbnail_path="crop.jpg", bbox=[1, 2, 3, 4], media_thumbnail_path="parent.jpg", media_width=2000, media_height=4000)
        result = SimilarPerson(id=8, name="Test", similarity=98.25, thumbnail="crop.jpg", profile_face=face).model_dump()
        self.assertEqual(result["thumbnail"], "crop.jpg")
        self.assertEqual(result["profile_face"]["media_thumbnail_path"], "parent.jpg")
        self.assertEqual(result["profile_face"]["bbox"], [1, 2, 3, 4])
