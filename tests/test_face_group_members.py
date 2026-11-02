"""Read-only member payload used by group review; synthetic SQLite fixtures."""
import unittest
import test_face_suggestions as fixtures
from app.services.face_review import get_groups


class FaceGroupMemberTests(unittest.TestCase):
    setUp = fixtures.FaceSuggestionsTests.setUp
    add_face = fixtures.FaceSuggestionsTests.add_face
    add_media = fixtures.FaceSuggestionsTests.add_media

    def test_group_contains_ordered_member_face_metadata(self):
        ids = [self.add_face(fixtures.embedding()) for _ in range(4)]
        group = get_groups(self.session)[0]
        self.assertEqual([face['id'] for face in group['member_faces']], ids)
        self.assertTrue(all(face['media_id'] and face['thumbnail_path'] for face in group['member_faces']))
        self.assertEqual(group['representative_face'], next(face for face in group['member_faces'] if face['id'] == group['representative_face']['id']))
