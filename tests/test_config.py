import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name
os.environ.pop("IS_DOCKER", None)

from app import config  # noqa: E402


class ScanSettingsTests(unittest.TestCase):
    def load_settings(
        self,
        file_config: dict,
        env_overrides: dict[str, str],
    ) -> config.AppSettings:
        env = {"XDG_CONFIG_HOME": _CONFIG_HOME.name, **env_overrides}
        with (
            patch.object(
                config,
                "load_config_from_file",
                return_value=file_config,
            ),
            patch.dict(os.environ, env, clear=True),
        ):
            return config.load_settings()

    def test_image_suffixes_include_webp(self) -> None:
        self.assertIn(".webp", config.ScanSettings().IMAGE_SUFFIXES)

    def test_env_overrides_match_existing_keys_case_insensitively(self) -> None:
        config_data = {
            "scan": {
                "auto_scan": False,
                "IMAGE_SUFFIXES": [".jpg"],
            }
        }

        with patch.dict(
            os.environ,
            {
                "OMOIDE_SCAN__AUTO_SCAN": "true",
                "OMOIDE_SCAN__IMAGE_SUFFIXES": ".jpg,.webp",
            },
            clear=True,
        ):
            config._apply_env_overrides(config_data)

        self.assertTrue(config_data["scan"]["auto_scan"])
        self.assertEqual(
            config_data["scan"]["IMAGE_SUFFIXES"],
            [".jpg", ".webp"],
        )
        self.assertNotIn("image_suffixes", config_data["scan"])

    def test_empty_config_honors_uppercase_and_lowercase_env_fields(
        self,
    ) -> None:
        settings = self.load_settings(
            {},
            {
                "OMOIDE_SCAN__AUTO_SCAN": "true",
                "OMOIDE_SCAN__IMAGE_SUFFIXES": ".png,.webp",
            },
        )

        self.assertTrue(settings.scan.auto_scan)
        self.assertEqual(settings.scan.IMAGE_SUFFIXES, [".png", ".webp"])

    def test_partial_config_is_recursively_merged_before_env_overrides(
        self,
    ) -> None:
        settings = self.load_settings(
            {
                "scan": {
                    "scan_interval_minutes": 42,
                }
            },
            {
                "OMOIDE_SCAN__AUTO_SCAN": "true",
                "OMOIDE_SCAN__IMAGE_SUFFIXES": ".jpg,.webp",
            },
        )

        self.assertEqual(settings.scan.scan_interval_minutes, 42)
        self.assertTrue(settings.scan.auto_scan)
        self.assertEqual(settings.scan.IMAGE_SUFFIXES, [".jpg", ".webp"])
        self.assertTrue(settings.scan.auto_rotate)


class DockerMediaSettingsTests(unittest.TestCase):
    def make_docker_settings(
        self,
        data_dir: Path,
        *,
        read_only: bool = False,
    ) -> config.GeneralSettings:
        with patch.object(config, "IS_DOCKER", True):
            return config.GeneralSettings(
                data_dir=data_dir,
                docker_media_read_only=read_only,
            )

    def test_docker_media_directory_can_be_declared_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_docker_settings(
                Path(temp_dir),
                read_only=True,
            )

        self.assertEqual(settings.media_dirs[0].path, Path("/app/media"))
        self.assertTrue(settings.media_dirs[0].read_only)
        with self.assertRaises(config.ReadOnlyMediaError):
            settings.ensure_media_path_writable(Path("/app/media/photo.jpg"))

    def test_docker_media_directory_remains_writable_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_docker_settings(Path(temp_dir))

        self.assertFalse(settings.media_dirs[0].read_only)
        settings.ensure_media_path_writable(Path("/app/media/photo.jpg"))

    def test_docker_media_read_only_is_excluded_from_model_dump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_docker_settings(
                Path(temp_dir),
                read_only=True,
            )

        self.assertNotIn("docker_media_read_only", settings.model_dump())


if __name__ == "__main__":
    unittest.main()
