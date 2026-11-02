"""Offline provider, validation, secret and hosted transport contracts."""
import base64
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from PIL import Image
from pydantic import ValidationError
from app.schemas.repair import RepairRequest
from app.services.upscalers import list_upscalers
from app.services.hypir import HypirClient
from app.services.comfy_annotation import ComfyAnnotationError
from app.config.schema import RepairSettings


class UpscalerTests(unittest.TestCase):
    def test_registry_and_unavailable_hosted(self):
        with patch('app.services.upscalers.hypir_api_key', return_value=''):
            providers = list_upscalers(set())
        self.assertEqual(len(providers), 4)
        self.assertFalse(next(p for p in providers if p['id'] == 'hypir:ultra')['available'])
        self.assertNotIn('api_key', json.dumps(providers))

    def test_registry_availability_is_per_provider(self):
        from app.config import settings
        with patch.object(settings.repairs, 'enabled', True), patch('app.services.upscalers.hypir_api_key', return_value='test-only'):
            providers = {p['id']:p for p in list_upscalers({'omoide-upscale-v1'})}
        self.assertTrue(providers['comfy:remacri-2x']['available'])
        self.assertFalse(providers['comfy:seedvr2-sharp']['available'])
        self.assertTrue(providers['hypir:balance']['available'])

    def test_hosted_readiness_does_not_require_local_bridge(self):
        from app.api.repairs import _ensure_provider_ready
        from fastapi import HTTPException
        request = RepairRequest(provider='hypir:balance', hosted_consent=True)
        with patch('app.api.repairs.hypir_api_key', return_value='test-only'), patch('app.api.repairs._bridge_ready') as bridge:
            _ensure_provider_ready(request)
            bridge.assert_not_called()
        with patch('app.api.repairs.hypir_api_key', return_value=''), self.assertRaises(HTTPException) as raised:
            _ensure_provider_ready(request)
        self.assertEqual(raised.exception.status_code,503)

    def test_env_key_is_execution_only_and_not_serialized(self):
        from app.config import settings
        from app.services.upscalers import hypir_api_key
        with patch.dict('os.environ', {'OMOIDE_HYPIR_API_KEY':'env-test-only'}):
            self.assertEqual(hypir_api_key(), 'env-test-only')
            self.assertNotIn('env-test-only', settings.model_dump_json())
            self.assertNotIn('env-test-only', repr(settings.model_dump(mode='python')))

    def test_existing_profiles_keep_their_original_inputs(self):
        from app.schemas.repair import BulkRepairRequest
        request = RepairRequest(profile='omoide-remove-text-v1', params={'seed':'42'})
        self.assertIsNone(request.provider)
        self.assertEqual(request.params.seed,42)
        request = BulkRepairRequest(profile='omoide-background-swap-v1', media_ids=[1], randomize_prompts=True)
        self.assertIsNone(request.provider)
        self.assertEqual(request.profile,'omoide-background-swap-v1')

    def test_config_read_masks_but_yaml_save_retains_configured_key(self):
        import asyncio
        from app.config import _sanitize_for_save
        from app.config.schema import AppSettings
        from app.api.config import get_settings
        config = AppSettings()
        config.repairs.hypir.api_key = __import__('pydantic').SecretStr('test-only-secret')
        with patch('app.api.config.settings', config):
            payload = asyncio.run(get_settings()).model_dump_json()
        self.assertNotIn('test-only-secret', payload)
        self.assertEqual(_sanitize_for_save(config)['repairs']['hypir']['api_key'], 'test-only-secret')

    def test_default_and_seed_scale_validation(self):
        req = RepairRequest(profile='omoide-upscale-v1')
        self.assertEqual(req.provider, 'comfy:remacri-2x')
        self.assertEqual(req.params.model_dump()['scale'], 2)
        req = RepairRequest(provider='comfy:seedvr2-sharp', params={'scale': 4})
        self.assertEqual(req.profile, 'omoide-upscale-seedvr2-v1')
        for provider, params in [('comfy:remacri-2x', {'scale':4}), ('comfy:seedvr2-sharp', {'scale':3}), ('hypir:ultra', {'texture_level':6}), ('hypir:balance', {'texture_level':3}), ('hypir:ultra', {'scale':True}), ('hypir:ultra', {'bogus':1})]:
            with self.subTest(provider=provider, params=params), self.assertRaises(ValidationError):
                RepairRequest(provider=provider, params=params, hosted_consent=True)

    def test_hosted_requires_per_run_consent(self):
        with self.assertRaises(ValidationError):
            RepairRequest(provider='hypir:ultra')
        with self.assertRaises(ValidationError):
            RepairRequest(profile='omoide-remove-text-v1', provider='hypir:ultra', hosted_consent=True)

    def test_upscaler_seed_validation_precedes_pydantic_coercion(self):
        for seed in [True, '42', 42.0]:
            with self.subTest(seed=seed), self.assertRaises(ValidationError):
                RepairRequest(provider='hypir:balance', hosted_consent=True, params={'seed':seed})

    def test_reserved_params_cannot_bypass_hosted_consent(self):
        with self.assertRaises(ValidationError):
            RepairRequest(profile='omoide-remove-text-v1', params={'provider':'hypir:ultra', 'hosted_consent':True})
        with self.assertRaises(ValidationError):
            RepairRequest(provider='hypir:ultra', hosted_consent='true')

    def test_secret_serialization_masks_key(self):
        config = RepairSettings(hypir={'api_key':'test-only-secret'})
        self.assertNotIn('test-only-secret', config.model_dump_json())
        self.assertNotIn('test-only-secret', repr(config.model_dump(mode='python')))
        self.assertNotIn('test-only-secret', repr(config))

    def test_hypir_payload_timeout_and_verified_image(self):
        output = io.BytesIO()
        Image.new('RGB', (4, 3)).save(output, 'PNG')
        response = io.BytesIO(json.dumps({'output':'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()}).encode())
        with patch('app.services.hypir.urlopen', return_value=response) as send:
            result = HypirClient('secret').repair('hypir:ultra', Image.new('RGB', (2, 2)), {'scale':2, 'texture_level':2, 'creativity_level':0, 'regeneration_level':0, 'seed':42, 'long_edge_size':-1, 'short_edge_size':-1, 'prompt':''})
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(send.call_args.kwargs['timeout'], 600)
        self.assertEqual(request.full_url, 'https://hypir.api.suppixel.ai/ultra')
        self.assertTrue(payload['input'].startswith('data:application/octet-stream;base64,'))
        self.assertEqual(payload['texture_level'], 2)
        self.assertEqual(payload['creativity_level'], 0)
        self.assertEqual(result.image, output.getvalue())

    def test_hypir_errors_never_echo_response_or_key(self):
        error = HTTPError('https://hypir.api.suppixel.ai/ultra', 429, 'secret', {}, io.BytesIO(b'secret'))
        with patch('app.services.hypir.urlopen', side_effect=error), self.assertRaises(ComfyAnnotationError) as raised:
            HypirClient('secret').repair('hypir:ultra', Image.new('RGB',(2,2)), {'scale':2})
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn('secret', str(raised.exception))
        with patch('app.services.hypir.urlopen', return_value=io.BytesIO(b'{"output":"data:image/png;base64,YmFk"}')), self.assertRaises(ComfyAnnotationError):
            HypirClient('secret').repair('hypir:ultra', Image.new('RGB',(2,2)), {'scale':2})


class HostedLineageTests(unittest.TestCase):
    def test_cancelled_hosted_response_never_reaches_copy_writer(self):
        from test_image_repairs import ImageRepairTests, FakeRepairClient
        from app.models import ImageRepairJob, ImageRepairStatus
        from app.tasks.image_repair import run_repair_job
        from sqlmodel import Session
        fixture = ImageRepairTests()
        fixture.setUp()
        try:
            job_id = fixture._job()
            with Session(fixture.engine) as session:
                job = session.get(ImageRepairJob, job_id)
                job.profile = 'omoide-upscale-hypir-ultra-v1'
                job.params = {'provider':'hypir:ultra', 'scale':2, 'hosted_consent':True}
                session.add(job)
                session.commit()
            def cancelled_response(*args, **kwargs):
                with Session(fixture.engine) as session:
                    job = session.get(ImageRepairJob, job_id)
                    job.status = ImageRepairStatus.CANCELLED
                    session.add(job)
                    session.commit()
                return FakeRepairClient().repair()
            with patch('app.database.engine', fixture.engine), patch('app.tasks.image_repair.HypirClient') as client, patch('app.tasks.image_repair.write_repaired') as writer:
                client.return_value.repair.side_effect = cancelled_response
                run_repair_job(job_id)
                writer.assert_not_called()
            with Session(fixture.engine) as session:
                job = session.get(ImageRepairJob,job_id)
                self.assertEqual(job.status, ImageRepairStatus.CANCELLED)
                self.assertIsNone(job.result_media_id)
        finally:
            fixture.tearDown()

    def test_hosted_job_uses_same_writer_and_preserves_provider(self):
        from test_image_repairs import ImageRepairTests, FakeRepairClient
        from app.config import settings
        from app.models import ImageRepairJob, Media, ImageRepairStatus
        from app.tasks.image_repair import run_repair_job
        from sqlmodel import Session
        fixture = ImageRepairTests()
        fixture.setUp()
        try:
            job_id = fixture._job()
            with Session(fixture.engine) as session:
                job = session.get(ImageRepairJob, job_id)
                job.profile = 'omoide-upscale-hypir-ultra-v1'
                job.params = {'provider':'hypir:ultra', 'scale':2, 'hosted_consent':True}
                session.add(job)
                session.commit()
            with patch('app.database.engine', fixture.engine), patch('app.tasks.image_repair.HypirClient') as client, patch('app.tasks.image_repair.repair_client') as bridge, patch.object(type(settings.general), 'resolved_media_dirs', return_value=[(fixture.root,False)]), patch('app.tasks.image_repair.generate_perceptual_hash', return_value='hash'), patch('app.tasks.image_repair.generate_thumbnail', return_value=(None,None)), patch('app.tasks.media_processing.run_processors_for_media'):
                client.return_value.repair.return_value = FakeRepairClient().repair()
                run_repair_job(job_id)
                bridge.assert_not_called()
            with Session(fixture.engine) as session:
                job = session.get(ImageRepairJob,job_id)
                self.assertEqual(job.status, ImageRepairStatus.SUCCEEDED)
                self.assertEqual(job.params['provider'], 'hypir:ultra')
                self.assertEqual(job.params['scale'], 2)
                result = session.get(Media,job.result_media_id)
                self.assertIn('upscale-hypir-ultra', result.filename)
                self.assertTrue(__import__('pathlib').Path(result.path).is_file())
        finally:
            fixture.tearDown()
