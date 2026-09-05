import importlib
import os
import tempfile
import unittest

from fastapi import HTTPException


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models import Person, PersonSocialLink  # noqa: E402
from app.schemas.person import SocialLinkCreate  # noqa: E402
from app.services.social_links import (  # noqa: E402
    derive_url,
    normalize_handle,
    suggest_from_paths,
)

person_api = importlib.import_module("app.api.person")


class SocialLinkServiceTests(unittest.TestCase):
    def test_normalize_handle_strips_at_whitespace_and_slashes(self) -> None:
        self.assertEqual(
            normalize_handle("instagram", "  @photo.person///  "),
            "photo.person",
        )

    def test_normalize_x_accepts_twitter_urls(self) -> None:
        self.assertEqual(
            normalize_handle("x", "https://twitter.com/photo_person/"),
            "photo_person",
        )

    def test_derive_platform_urls(self) -> None:
        expected = {
            "instagram": "https://instagram.com/person.name",
            "tiktok": "https://tiktok.com/@person.name",
            "x": "https://x.com/person.name",
            "youtube": "https://youtube.com/@person.name",
            "onlyfans": "https://onlyfans.com/person.name",
            "threads": "https://threads.net/@person.name",
            "facebook": "https://facebook.com/person.name",
            "snapchat": "https://snapchat.com/add/person.name",
        }
        for platform, url in expected.items():
            with self.subTest(platform=platform):
                self.assertEqual(derive_url(platform, "@person.name/"), url)

    def test_other_requires_explicit_url(self) -> None:
        with self.assertRaises(ValueError):
            derive_url("other", "person.name")

    def test_suggestions_group_handle_folders_and_infer_platform(self) -> None:
        suggestions = suggest_from_paths(
            [
                "/media/Instagram/person.name/one.jpg",
                "/media/Instagram/person.name/two.jpg",
                "/media/TikTok/second_user/three.mp4",
                "/media/Downloads/four.jpg",
                "/media/DCIM/12345/five.jpg",
                "/media/archive/not a handle/six.jpg",
            ]
        )

        self.assertEqual(
            [suggestion.model_dump() for suggestion in suggestions],
            [
                {
                    "platform": "instagram",
                    "handle": "person.name",
                    "source_folder": "person.name",
                    "media_count": 2,
                },
                {
                    "platform": "tiktok",
                    "handle": "second_user",
                    "source_folder": "second_user",
                    "media_count": 1,
                },
            ],
        )


class SocialLinkApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[Person.__table__, PersonSocialLink.__table__],
        )
        self.session = Session(self.engine)
        self.person = Person(name="Linked person", appearance_count=0)
        self.session.add(self.person)
        self.session.commit()
        self.session.refresh(self.person)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_duplicate_rejected_after_handle_normalization(self) -> None:
        first = person_api.add_social_link(
            self.person.id,
            SocialLinkCreate(platform="instagram", handle="@person.name"),
            self.session,
        )
        self.assertEqual(first.url, "https://instagram.com/person.name")

        with self.assertRaises(HTTPException) as raised:
            person_api.add_social_link(
                self.person.id,
                SocialLinkCreate(platform="instagram", handle="person.name/"),
                self.session,
            )
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
