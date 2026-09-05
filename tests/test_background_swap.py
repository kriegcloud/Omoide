import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.api.repairs import (
    _params_for_media,
    _randomized_background_params,
    health,
    start_repair,
)
from app.config import settings
from app.models import Face, Media, Person
from app.schemas.repair import BulkRepairRequest, RepairParams, RepairRequest
from app.services.background_prompts import load_prompts
from app.services.comfy_repair import SUPPORTED_REPAIR_PROFILES
from app.services.image_edits import write_repaired
from PIL import Image


PROFILE = "omoide-background-swap-v1"


class BackgroundSwapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "portrait.jpg"
        Image.new("RGB", (100, 80), "blue").save(self.source)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_prompt_is_required_unless_bulk_randomises(self):
        with self.assertRaises(ValidationError):
            RepairRequest(profile=PROFILE, person_id=1)
        request = BulkRepairRequest(
            profile=PROFILE,
            person_id=1,
            media_ids=[1],
            randomize_prompts=True,
        )
        self.assertTrue(request.randomize_prompts)

    def test_prompt_and_seed_bounds(self):
        with self.assertRaises(ValidationError):
            RepairRequest(profile=PROFILE, person_id=1, params={"prompt": "x" * 2001})
        with self.assertRaises(ValidationError):
            RepairRequest(profile=PROFILE, person_id=1, params={"prompt": "valid", "seed": -1})
        request = RepairRequest(
            profile=PROFILE,
            person_id=1,
            params={"prompt": "x" * 2000, "seed": 0},
        )
        self.assertEqual(request.params.seed, 0)

    def test_bulk_randomisation_is_stable_per_media_id(self):
        params = RepairParams()
        with patch("app.api.repairs.secrets.randbits", side_effect=[11, 12, 13]):
            first = _randomized_background_params(42, params)
            retry = _randomized_background_params(42, params)
            other = _randomized_background_params(43, params)
        self.assertEqual(first.prompt, retry.prompt)
        self.assertNotEqual(first.seed, retry.seed)
        self.assertIn(other.prompt, load_prompts())

    def test_subject_box_reuses_remove_people_derivation(self):
        with Session(self.engine) as session:
            person = Person(name="Subject", appearance_count=1)
            media = Media(
                path=str(self.source),
                filename=self.source.name,
                size=1,
                width=2560,
                height=1280,
            )
            session.add(person)
            session.add(media)
            session.commit()
            session.add(Face(media_id=media.id, person_id=person.id, bbox=[100, 50, 200, 300]))
            session.commit()
            params = _params_for_media(
                session,
                media,
                RepairRequest(
                    profile=PROFILE,
                    person_id=person.id,
                    params={"prompt": "Replace the background."},
                ),
            )
        self.assertEqual(
            params["subject_box"],
            {"x": 200, "y": 100, "width": 400, "height": 600},
        )
        self.assertIsInstance(params["seed"], int)

    def test_prompt_library_and_repaired_name(self):
        prompts = load_prompts()
        self.assertGreaterEqual(len(prompts), 25)
        self.assertTrue(all("Keep the person, their clothing and pose exactly the same." in prompt for prompt in prompts))
        target = write_repaired(
            self.source,
            Image.new("RGB", (10, 10)),
            PROFILE,
            media_roots=[(self.root, False)],
        )
        self.assertEqual(target.name, "portrait_repaired-background-swap.jpg")

    def test_profile_is_supported_and_listed_by_health(self):
        self.assertIn(PROFILE, SUPPORTED_REPAIR_PROFILES)
        old_enabled = settings.repairs.enabled
        try:
            settings.repairs.enabled = True
            with patch(
                "app.api.repairs.repair_client",
                return_value=SimpleNamespace(
                    health=lambda: SimpleNamespace(ready=True, profiles=[PROFILE])
                ),
            ):
                result = health()
            self.assertTrue(result.ready)
            self.assertIn(PROFILE, result.profiles)
        finally:
            settings.repairs.enabled = old_enabled

    def test_disabled_backend_returns_503(self):
        old_presentation = settings.general.presentation_mode
        old_enabled = settings.repairs.enabled
        try:
            settings.general.presentation_mode = False
            settings.repairs.enabled = False
            with self.assertRaises(HTTPException) as raised:
                start_repair(
                    1,
                    RepairRequest(
                        profile=PROFILE,
                        person_id=1,
                        params={"prompt": "Replace the background."},
                    ),
                    BackgroundTasks(),
                    None,
                )
            self.assertEqual(raised.exception.status_code, 503)
        finally:
            settings.general.presentation_mode = old_presentation
            settings.repairs.enabled = old_enabled


if __name__ == "__main__":
    unittest.main()
