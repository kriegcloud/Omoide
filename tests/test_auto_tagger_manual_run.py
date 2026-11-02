"""A manual "Auto Tags" run must tag even when automatic tagging is off.

run_single_processor called load_model(), which honours
tagging.auto_tagging=False by loading no tags, then forced active=True. The
tagger then stamped ran_auto_tagging on every media with zero tags attached,
so later real runs skipped the whole library (2026-11-02: 8,702 media, 0
tag links).
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from app.config import settings
from app.processors import auto_tagger as auto_tagger_mod
from app.processors.auto_tagger import AutoTagger
from app.tasks import media_processing


def _fake_vectors(tags):
    return {tag: np.ones(4, dtype=np.float32) / 2 for tag in tags}


class AutoTaggerManualRunTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(settings.tagging, "auto_tagging", False))
        self.enterContext(patch.object(settings.tagging, "use_default_tags", True))
        self.enterContext(patch.object(auto_tagger_mod, "get_clip_encoder", return_value=object()))
        self.enterContext(patch.object(auto_tagger_mod, "build_tag_vector_map", side_effect=_fake_vectors))
        self.enterContext(patch.object(AutoTagger, "_calibrate_thresholds", return_value=None))

    def test_load_model_respects_disabled_config(self):
        tagger = AutoTagger()
        tagger.load_model()
        self.assertFalse(tagger.active)

    def test_manual_run_loads_tags_despite_disabled_config(self):
        tagger = AutoTagger()
        tagger.load_for_manual_run()
        self.assertTrue(tagger.active)
        self.assertIsNotNone(tagger._tag_matrix)
        self.assertGreater(len(tagger._tag_names), 0)

    def test_unloaded_tagger_does_not_burn_the_completion_flag(self):
        tagger = AutoTagger()
        tagger.load_model()  # config off: no tags loaded
        tagger.active = True  # the old manual-run override
        media = SimpleNamespace(id=1, path="/m/a.jpg", ran_auto_tagging=False, embeddings_created=True)
        session = MagicMock()
        session.exec.return_value.first.return_value = (b"\x00" * 16,)
        with patch.object(auto_tagger_mod, "vector_from_stored", return_value=np.ones(4, dtype=np.float32)):
            tagger.process(media, session, scenes=[])
        self.assertFalse(media.ran_auto_tagging)

    def test_single_processor_run_uses_manual_loader(self):
        target = MagicMock()
        target.name = "auto_tagger"
        media_processing._activate_for_manual_run(target)
        target.load_for_manual_run.assert_called_once_with()

    def test_manual_loader_falls_back_to_load_model(self):
        target = SimpleNamespace(name="x", active=False, loads=[])
        target.load_model = lambda: target.loads.append(1)
        media_processing._activate_for_manual_run(target)
        self.assertEqual(target.loads, [1])
        self.assertTrue(target.active)


if __name__ == "__main__":
    unittest.main()
