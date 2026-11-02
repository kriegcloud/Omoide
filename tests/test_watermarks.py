import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from app.services.watermarks import normalize_handle, select_regions, build_mask, hard_composite, apply_image, restore_image

class WatermarkAlgorithmsTest(unittest.TestCase):
    def test_candidates(self):
        def region(text, y): return {'text': text, 'quad': [200,y,270,y,270,y+8,200,y+8]}
        regions = [region('onlyfans.com/name', 490), region('onlyfans.com/name',250),region('x',490),region('onlvfans.com/name',250)]
        self.assertEqual(len(select_regions(regions, (500,500), [])), 1)
        self.assertEqual(len(select_regions(regions, (500,500), ['onlyfanscomname'])), 3)
        self.assertEqual(normalize_handle(' @A.b / C '), 'abc')
    def test_mask(self):
        im = Image.new('RGB',(500,500),'gray')
        mask = build_mask(im, [{'quad':[10,480,80,480,80,490,10,490]}])
        self.assertGreater(np.count_nonzero(mask),0)
        with self.assertRaises(ValueError): build_mask(im,[{'quad':[0,0,500,0,500,500,0,500]}])
    def test_composite(self):
        source = Image.new('RGB',(20,20),'red'); edited=Image.new('RGB',source.size,'blue')
        mask=Image.new('L',source.size); mask.putpixel((5,5),255)
        out=hard_composite(source, edited, mask)
        self.assertEqual(out.getpixel((0,0)), source.getpixel((0,0)))
        self.assertEqual(out.getpixel((5,5)), edited.getpixel((5,5)))
    def test_apply_restore_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'photo.jpg'; exif=Image.Exif(); exif[270]='keep this'
            Image.new('RGB',(20,20),'red').save(p,exif=exif)
            original=p.read_bytes()
            backup, before, after=apply_image(p,Image.new('RGB',(20,20),'blue'))
            self.assertEqual(backup.parent.name,'.beep-originals')
            with Image.open(p) as image: self.assertEqual(image.getexif()[270],'keep this')
            restore_image(p,backup,after)
            self.assertEqual(p.read_bytes(),original)

    def test_png_alpha_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'photo.png'
            pixels=np.zeros((20,20,4),dtype=np.uint8); pixels[:,:,0]=100
            pixels[:,:,3]=np.arange(20,dtype=np.uint8)[None,:]*10
            Image.fromarray(pixels).save(p)
            apply_image(p,Image.new('RGB',(20,20),'blue'))
            with Image.open(p) as result:
                np.testing.assert_array_equal(np.asarray(result)[:,:,3],pixels[:,:,3])

    def test_changed_source_refuses_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'photo.png'; Image.new('RGB',(20,20),'red').save(p)
            original=p.read_bytes()
            with self.assertRaises(ValueError): apply_image(p,Image.new('RGB',(20,20)),'stale')
            self.assertEqual(p.read_bytes(),original)
            self.assertFalse((p.parent/'.beep-originals').exists())

    def test_preview_fake_client_preserves_original_and_raw_orientation(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.services.watermarks import preview_candidate
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'photo.jpg'; exif=Image.Exif(); exif[274]=6
            Image.new('RGB',(500,300),'gray').save(p,exif=exif)
            original=p.read_bytes()
            candidate=SimpleNamespace(id=1,regions=[{'quad':[200,280,270,280,270,290,200,290]}])
            session=SimpleNamespace(add=lambda value: None,commit=lambda:None)
            def inpaint(image,mask):
                self.assertEqual(image.size,(500,300)); self.assertFalse(image.info)
                return Image.new('RGB',image.size,'white')
            client=SimpleNamespace(inpaint=inpaint)
            with patch('app.services.vision_client.get_vision_client',return_value=client),patch('app.config.settings',SimpleNamespace(general=SimpleNamespace(thumb_dir=Path(tmp)/'thumbs'))):
                preview_candidate(session,SimpleNamespace(path=str(p)),candidate)
            self.assertEqual(p.read_bytes(),original)
            self.assertTrue(candidate.preview_path.endswith('.jpg'))
            self.assertTrue((Path(tmp)/'thumbs'/candidate.mask_path).exists())

    def test_handle_punctuation(self):
        self.assertEqual(normalize_handle('@Some_handle.name'), 'somehandlename')

    def test_restore_changed_cleaned_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'photo.png'; Image.new('RGB',(20,20),'red').save(p)
            backup,before,after=apply_image(p,Image.new('RGB',(20,20),'blue'))
            p.write_bytes(b'changed')
            with self.assertRaises(ValueError): restore_image(p,backup,after,before)
            self.assertTrue(backup.exists())
            self.assertEqual(p.read_bytes(),b'changed')

from round6_support import DatabaseCase

class WatermarkIntegrationTest(DatabaseCase):
    def test_approve_restore_receipts_and_learned_handle(self):
        from uuid import uuid4
        from unittest.mock import patch
        from types import SimpleNamespace
        from sqlmodel import select
        from app.models import WatermarkCandidate, WatermarkHandle
        from app.services.mutation_operations import begin_operation, lookup_operation
        from app.tasks.watermarks import run_mutation
        media=self.media()
        Image.new('RGB',(500,300),'gray').save(media.path)
        original=Path(media.path).read_bytes()
        mask=Image.new('L',(500,300)); mask.paste(255,(200,280,270,290)); mask.save(self.root/'mask.png')
        candidate=WatermarkCandidate(media_id=media.id,regions=[{'text':'@some_handle','quad':[200,280,270,280,270,290,200,290]}],
            original_sha256=__import__('hashlib').sha256(original).hexdigest(),mask_path='mask.png',preview_path='preview.jpg')
        self.session.add(candidate); self.session.commit(); self.session.refresh(candidate)
        cid,mid=candidate.id,media.id
        client=SimpleNamespace(inpaint=lambda image,mask: Image.new('RGB',image.size,'white'))
        approve_id=str(uuid4())
        begin_operation(self.session,approve_id,{'action':'approve','candidate_ids':[cid]},[mid])
        with patch('app.tasks.watermarks.db.engine',self.engine),patch('app.services.vision_client.get_vision_client',return_value=client),patch('app.services.watermarks.refresh_media') as refresh:
            run_mutation(approve_id)
            refresh.assert_called_once()
        self.session.expire_all(); self.session.refresh(candidate)
        self.assertEqual(candidate.status,'applied')
        self.assertEqual(lookup_operation(self.session,approve_id).state,'succeeded')
        self.assertEqual(lookup_operation(self.session,approve_id).applied,[f'media:{mid}'])
        self.assertEqual(self.session.exec(select(WatermarkHandle)).one().text_normalized,'somehandle')
        self.assertEqual(Path(candidate.backup_path).read_bytes(),original)
        restore_id=str(uuid4())
        begin_operation(self.session,restore_id,{'action':'restore','candidate_ids':[cid]},[mid])
        with patch('app.tasks.watermarks.db.engine',self.engine),patch('app.services.watermarks.refresh_media'):
            run_mutation(restore_id)
        self.session.expire_all();self.session.refresh(candidate)
        self.assertEqual(candidate.status,'restored')
        self.assertEqual(Path(media.path).read_bytes(),original)
        self.assertEqual(lookup_operation(self.session,restore_id).state,'succeeded')

    def test_mask_edit_validates_and_regenerates_preview(self):
        import base64
        from io import BytesIO
        from unittest.mock import patch
        from types import SimpleNamespace
        from app.models import WatermarkCandidate
        from app.api.watermarks import edit_mask, MaskEdit
        media=self.media(); Image.new('RGB',(500,300),'gray').save(media.path)
        candidate=WatermarkCandidate(media_id=media.id,regions=[])
        self.session.add(candidate);self.session.commit();self.session.refresh(candidate)
        mask=Image.new('L',(500,300));mask.paste(255,(200,280,270,290))
        buffer=BytesIO();mask.save(buffer,format='PNG')
        client=SimpleNamespace(inpaint=lambda image,mask: image)
        with patch('app.services.vision_client.get_vision_client',return_value=client):
            result=edit_mask(candidate.id,MaskEdit(mask_png_base64=base64.b64encode(buffer.getvalue()).decode()),self.session)
        self.assertTrue((self.root/result.preview_path).exists())
        self.assertTrue((self.root/result.mask_path).exists())

    def test_png_la_and_palette_transparency(self):
        for mode in ('LA','P'):
            with self.subTest(mode=mode):
                path=self.root/f'{mode}.png'
                if mode=='LA': image=Image.new('LA',(20,20),(120,80))
                else:
                    image=Image.new('P',(20,20),0); image.info['transparency']=0
                image.save(path)
                with Image.open(path) as source: expected=np.asarray(source.convert('RGBA'))[:,:,3].copy()
                apply_image(path,Image.new('RGB',(20,20),'blue'))
                with Image.open(path) as result: np.testing.assert_array_equal(np.asarray(result.convert('RGBA'))[:,:,3],expected)
