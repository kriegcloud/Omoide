"""Exact video face frame route, cache and detector coordinate regressions."""
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np
import httpx
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from sqlmodel import Session, SQLModel, create_engine

from app.api.face_frames import face_frame, router
from app.database import get_session
from app.face_frames import extract_face_frame
from app.frames import _frame_result
from app.models import Face, Media
from app.services.face_working_image import face_working_image


class FaceFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.source = self.root / 'video.mp4'
        self.source.write_bytes(b'fixture')
        self.media = Media(path=str(self.source), filename='video.mp4', size=7, duration=2)
        self.session.add(self.media)
        self.session.commit()
        self.face = Face(media_id=self.media.id, bbox=[320, 180, 160, 90], timestamp=0.75)
        self.session.add(self.face)
        self.session.commit()
        self.config = patch('app.api.face_frames.settings', SimpleNamespace(general=SimpleNamespace(thumb_dir=self.root / 'thumbs')))
        self.config.start()
        self.addCleanup(self.config.stop)

    def request(self, etag=None):
        return Request({'type': 'http', 'headers': [] if etag is None else [(b'if-none-match', etag.encode())]})

    def get(self, etag=None, face_id=None):
        return face_frame(self.face.id if face_id is None else face_id, self.request(etag), self.session)

    def fake_extract(self, source, timestamp, target):
        self.assertEqual(source, self.source)
        self.assertEqual(timestamp, .75)
        cv2.imwrite(str(target), np.zeros((720, 1280, 3), np.uint8))

    def test_cache_conditional_get_and_source_invalidation(self):
        with patch('app.api.face_frames.extract_face_frame', side_effect=self.fake_extract) as extract:
            response = self.get()
            self.assertEqual(response.media_type, 'image/jpeg')
            self.assertEqual(response.headers['x-face-frame-width'], '1280')
            self.assertEqual(response.headers['x-face-frame-height'], '720')
            self.assertEqual(response.headers['x-face-box-space'], 'detector')
            self.assertTrue(Path(response.path).is_file())
            etag = response.headers['etag']
            self.assertEqual(self.get('"other", W/' + etag).status_code, 304)
            self.assertEqual(self.get('*').status_code, 304)
            self.assertEqual(self.get('"other"').status_code, 200)
            self.assertEqual(extract.call_count, 1)
            self.source.write_bytes(b'changed source')
            self.assertNotEqual(self.get().headers['etag'], etag)
            self.assertEqual(extract.call_count, 2)

    def assert_missing(self):
        with self.assertRaises(HTTPException) as error:
            self.get()
        self.assertEqual(error.exception.status_code, 404)

    def test_404_missing_face_photo_timestamp_flagged_and_absent_source(self):
        with self.assertRaises(HTTPException) as error:
            self.get(face_id=9999)
        self.assertEqual(error.exception.status_code, 404)
        for obj, attr, value in [(self.face, 'timestamp', None), (self.media, 'duration', None), (self.media, 'missing_since', datetime.now())]:
            old = getattr(obj, attr)
            setattr(obj, attr, value)
            self.session.add(obj)
            self.session.commit()
            self.assert_missing()
            setattr(obj, attr, old)
            self.session.add(obj)
            self.session.commit()
        with patch('app.api.face_frames.extract_face_frame', side_effect=self.fake_extract):
            self.get()
        self.source.unlink()
        self.assert_missing()  # Even an already cached frame must respect missing media.

    def test_http_router_serves_jpeg_and_304_without_body(self):
        app = FastAPI()
        app.include_router(router, prefix='/api/faces')

        async def session_override():
            yield self.session

        app.dependency_overrides[get_session] = session_override

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get(f'/api/faces/{self.face.id}/frame')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'image/jpeg')
                self.assertTrue(response.content.startswith(b'\xff\xd8'))
                cached = await client.get(f'/api/faces/{self.face.id}/frame', headers={'If-None-Match': response.headers['etag']})
                self.assertEqual(cached.status_code, 304)
                self.assertEqual(cached.content, b'')
                self.assertEqual((await client.get('/api/faces/9999/frame')).status_code, 404)

        async def inline_worker(func, *args, **_kwargs):
            # The managed sandbox stalls AnyIO worker wakeups. Exercise actual
            # routing/body/conditional semantics inline; live worker proof remains
            # an integration follow-up outside this sandbox.
            return func(*args)

        with patch('app.api.face_frames.extract_face_frame', side_effect=self.fake_extract), patch('anyio.to_thread.run_sync', side_effect=inline_worker):
            asyncio.run(asyncio.wait_for(exercise(), timeout=10))

    def test_failed_extraction_does_not_cache(self):
        with patch('app.api.face_frames.extract_face_frame', side_effect=RuntimeError('decode failed')):
            self.assert_missing()
        self.assertEqual(list((self.root / 'thumbs').rglob('*.jpg')), [])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_real_ffmpeg_exact_time_and_detector_coordinate_size(self):
        # Two distinguishable seconds; timestamp must select the second, not the poster.
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'color=red:s=1920x1080:r=4:d=1', '-f', 'lavfi', '-i',
                        'color=blue:s=1920x1080:r=4:d=1', '-filter_complex',
                        '[0:v][1:v]concat=n=2:v=1:a=0', '-threads', '1', str(self.source)], check=True, timeout=30)
        target = self.root / 'frame.jpg'
        extract_face_frame(self.source, 1.25, target)
        image = cv2.imread(str(target))
        self.assertEqual(image.shape[:2], (720, 1280))
        self.assertGreater(float(image[:, :, 0].mean()), 200)
        self.assertLess(float(image[:, :, 2].mean()), 20)

    def test_coordinate_size_matches_processor_even_without_ffmpeg(self):
        # Compare the exact detector preparation path, including floor rounding.
        with patch('app.frames.settings', SimpleNamespace(general=SimpleNamespace(thumb_dir=self.root))):
            detected = _frame_result(np.zeros((1081, 1920, 3), np.uint8), 0, [0.], 2., 1)[2]
        odd = self.root / 'odd.jpg'
        with patch('app.face_frames.run_silent') as run, patch('app.face_frames.ensure_ffmpeg_available', return_value=Path('/mock/ffmpeg')):
            ok, data = cv2.imencode('.jpg', np.zeros((1081, 1920, 3), np.uint8))
            self.assertTrue(ok)
            run.return_value = subprocess.CompletedProcess([], 0, data.tobytes(), b'')
            extract_face_frame(self.source, .75, odd)
        self.assertEqual(cv2.imread(str(odd)).shape, detected.shape)
        self.assertEqual(cv2.imread(str(odd)).shape, face_working_image(np.zeros((1081, 1920, 3), np.uint8)).shape)
        self.assertEqual(detected.shape[:2], (720, 1280))
