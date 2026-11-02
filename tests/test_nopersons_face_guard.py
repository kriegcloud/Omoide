"""A detected face always means a person: media 8780 (a video whose scene keyframes
all landed on an end card) was listed as person-free although it had a face and its
poster frame scores 0.92 on the person detector."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image


class NoPersonsFaceGuardTests(unittest.TestCase):
    def test_media_with_a_detected_face_is_never_person_free(self):
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel, Session, select
        from app.models import Face, Media
        from app.api.nopersons import _base_filter
        engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for i in (1, 2):
                session.add(Media(id=i, filename=f'{i}.mp4', path=f'/{i}.mp4', size=1, duration=6.8,
                                  persons_detected=True, has_persons=False))
            session.add(Face(id=10, media_id=2, thumbnail_path='f.jpg', bbox=[0, 0, 5, 5]))
            session.commit()
            ids = [m.id for m in session.exec(select(Media).where(_base_filter(None, 'processed')))]
            self.assertEqual(ids, [1])

    def test_video_sampling_includes_the_poster_frame(self):
        from app.processors import person_detection as pd
        with tempfile.TemporaryDirectory() as tmp:
            thumbs = Path(tmp)
            (thumbs / 'media').mkdir()
            Image.new('RGB', (36, 64), (200, 10, 10)).save(thumbs / 'media' / 'poster.jpg')
            media = SimpleNamespace(duration=6.8, thumbnail_path='media/poster.jpg')
            scenes = [(None, np.zeros((8, 8, 3), dtype=np.uint8)) for _ in range(4)]
            with patch.object(type(pd.settings.general), 'thumb_dir', property(lambda _self: thumbs)):
                images = pd.detector_images(media, scenes)
        self.assertEqual(len(images), 5)
        self.assertEqual(images[0].getpixel((0, 0))[0] > 150, True)

    def test_video_without_poster_still_uses_scene_frames(self):
        from app.processors import person_detection as pd
        media = SimpleNamespace(duration=30, thumbnail_path=None)
        scenes = [(None, np.zeros((8, 8, 3), dtype=np.uint8)) for _ in range(12)]
        self.assertEqual(len(pd.detector_images(media, scenes)), 5)


if __name__ == '__main__':
    unittest.main()
