"""Derivative refresh and app-wide route guards without sockets or GPU models."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from fastapi import HTTPException
from starlette.requests import Request

from app.services import watermarks as wm
from round6_support import DatabaseCase


class WatermarkRefreshTests(unittest.TestCase):
    def test_face_boxes_use_xywh_and_nonoverlap_keeps_faces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'photo.png'
            Image.new('RGB',(500,300)).save(path)
            for overlap in (False,True):
                with self.subTest(overlap=overlap):
                    media=SimpleNamespace(id=1,path=str(path))
                    mask=Image.new('L',(500,300))
                    mask.putpixel((220,120) if overlap else (2,2),255)
                    session=Mock()
                    session.exec.return_value.all.return_value=[SimpleNamespace(bbox=[200,100,50,50])]
                    photo=SimpleNamespace(rgb=np.zeros((300,500,3),dtype=np.uint8),image=Mock())
                    def quality_process(row,session,scenes): row.quality_score=.8
                    quality=Mock();quality.process.side_effect=quality_process
                    with patch('app.phash.generate_perceptual_hash',return_value='hash'),patch('app.sharpness.score_image_path',return_value=10),patch('app.thumbnails.generate_thumbnail',return_value=('thumb.jpg',None)),patch('app.frames.load_photo_scene',return_value=photo),patch('app.processors.embedding_extractor.EmbeddingExtractor') as embeddings,patch('app.processors.quality.QualityProcessor',return_value=quality),patch('app.processors.faces.FaceProcessor') as faces:
                        wm.refresh_media(session,media,mask)
                        self.assertEqual(faces.called,overlap)
                        if overlap:
                            faces.return_value.reset_for_media.assert_called_once_with(media,session)
                            faces.return_value.process.assert_called_once()
                    self.assertEqual(media.phash,'hash')
                    self.assertFalse(media.persons_detected)
                    self.assertEqual(media.size,path.stat().st_size)

    def test_every_watermark_mutation_inherits_presentation_guard(self):
        from app.main import app
        from app.config import require_mutation_allowed,settings
        import asyncio
        from app.api.watermarks import router
        paths = app.openapi()['paths']
        self.assertIn('/api/watermarks/approve', paths)
        self.assertIn(require_mutation_allowed, [d.dependency for d in app.router.dependencies])
        writes=0
        for route in router.routes:
            for method in route.methods-{'GET','HEAD','OPTIONS'}:
                writes+=1
                request=Request({'type':'http','method':method,'path':'/api/watermarks'+route.path,'headers':[],'endpoint':route.endpoint})
                with patch.object(settings.general,'presentation_mode',True),self.assertRaises(HTTPException) as caught:
                    asyncio.run(require_mutation_allowed(request))
                self.assertEqual(caught.exception.status_code,403)
        self.assertGreaterEqual(writes,7)


class WatermarkFailureReceiptTests(DatabaseCase):
    def test_derivative_failure_keeps_backup_and_unknown_receipt(self):
        from hashlib import sha256
        from uuid import uuid4
        from app.models import WatermarkCandidate
        from app.services.mutation_operations import begin_operation, lookup_operation
        from app.tasks.watermarks import run_mutation
        media=self.media()
        Image.new('RGB',(500,300),'gray').save(media.path)
        original=Path(media.path).read_bytes()
        mask=Image.new('L',(500,300));mask.paste(255,(200,280,270,290));mask.save(self.root/'mask.png')
        candidate=WatermarkCandidate(media_id=media.id,regions=[],mask_path='mask.png',preview_path='preview.jpg',original_sha256=sha256(original).hexdigest())
        self.session.add(candidate);self.session.commit();self.session.refresh(candidate)
        operation_id=str(uuid4())
        begin_operation(self.session,operation_id,{'action':'approve','candidate_ids':[candidate.id]},[media.id])
        client=SimpleNamespace(inpaint=lambda image,mask:Image.new('RGB',image.size,'white'))
        with patch('app.tasks.watermarks.db.engine',self.engine),patch('app.services.vision_client.get_vision_client',return_value=client),patch.object(wm,'refresh_media',side_effect=RuntimeError('worker unavailable')):
            run_mutation(operation_id)
            # Re-entry must never apply the same uncertain mutation twice.
            run_mutation(operation_id)
        self.session.expire_all();self.session.refresh(candidate)
        self.assertEqual(candidate.status,'applied')
        self.assertIn('worker unavailable',candidate.error)
        self.assertEqual(Path(candidate.backup_path).read_bytes(),original)
        receipt=lookup_operation(self.session,operation_id)
        self.assertEqual(receipt.state,'unknown')
        self.assertEqual(receipt.unknown,[f'media:{media.id}'])
        self.assertEqual(len(list((Path(media.path).parent/'.beep-originals').iterdir())),1)

    def test_handle_crud_normalizes_and_refuses_duplicate(self):
        from app.api.watermarks import create_handle,get_handle,update_handle,delete_handle,HandleInput
        first=create_handle(HandleInput(display_text='@some_handle'),self.session)
        other=create_handle(HandleInput(display_text='@other'),self.session)
        self.assertEqual(get_handle(first.id,self.session).text_normalized,'somehandle')
        with self.assertRaises(HTTPException):
            update_handle(first.id,HandleInput(display_text='other'),self.session)
        changed=update_handle(first.id,HandleInput(display_text='new.name'),self.session)
        self.assertEqual(changed.text_normalized,'newname')
        delete_handle(other.id,self.session)
        with self.assertRaises(HTTPException):get_handle(other.id,self.session)

    def test_edit_similarity_uses_substitution_distance(self):
        self.assertAlmostEqual(wm.edit_similarity('abcde','abxye'),.6)
        self.assertAlmostEqual(wm.edit_similarity('abcde','abcxe'),.8)
