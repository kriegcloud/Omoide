import importlib
import os
import tempfile
import unittest

from fastapi import HTTPException


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models import Person, PersonSocialLink, SocialBrand  # noqa: E402
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
            tables=[Person.__table__, PersonSocialLink.__table__, SocialBrand.__table__],
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

class UrlSocialLinkApiTests(SocialLinkApiTests):
    def setUp(self):
        super().setUp()
        from app.models import SocialBrand
        SocialBrand.__table__.create(self.engine, checkfirst=True)

    def test_url_dedupe_edit_order_and_brand(self):
        from app.api.person import extensions as api
        from app.schemas.person import SocialLinkUpdate, SocialLinkReorder
        first = api.add_social_link(self.person.id, SocialLinkCreate(url='twitter.com/person?utm_source=x'), self.session)
        self.assertEqual(first.brand.simple_icons_slug, 'x')
        with self.assertRaises(HTTPException) as raised:
            api.add_social_link(self.person.id, SocialLinkCreate(url='x.com/person/'), self.session)
        self.assertEqual(raised.exception.status_code, 409)
        second = api.add_social_link(self.person.id, SocialLinkCreate(url='example.com/me'), self.session)
        edited = api.update_social_link(self.person.id, second.id, SocialLinkUpdate(label='My site'), self.session)
        self.assertEqual(edited.label, 'My site')
        result = api.reorder_social_links(self.person.id, SocialLinkReorder(link_ids=[second.id, first.id]), self.session)
        self.assertEqual([link.id for link in result], [second.id, first.id])
        with self.assertRaises(HTTPException):
            api.reorder_social_links(self.person.id, SocialLinkReorder(link_ids=[second.id]), self.session)

    def test_preview_is_pure_and_update_respects_ownership(self):
        from app.api.person import extensions as api
        from app.schemas.person import SocialLinkPreviewRequest, SocialLinkUpdate
        preview = api.preview_social_link(SocialLinkPreviewRequest(url='@person'))
        self.assertEqual(preview.platform, 'instagram')
        link = api.add_social_link(self.person.id, SocialLinkCreate(url='example.com'), self.session)
        other = Person(name='Other', appearance_count=0)
        self.session.add(other); self.session.commit()
        with self.assertRaises(HTTPException) as raised:
            api.update_social_link(other.id, link.id, SocialLinkUpdate(label='wrong'), self.session)
        self.assertEqual(raised.exception.status_code, 404)

    def test_edit_redetects_platform_and_rejects_duplicate(self):
        from app.api.person import extensions as api
        from app.schemas.person import SocialLinkUpdate
        first = api.add_social_link(self.person.id, SocialLinkCreate(url='instagram.com/person'), self.session)
        second = api.add_social_link(self.person.id, SocialLinkCreate(url='example.com/me'), self.session)
        with self.assertRaises(HTTPException) as raised:
            api.update_social_link(self.person.id, second.id, SocialLinkUpdate(url='www.instagram.com/person/'), self.session)
        self.assertEqual(raised.exception.status_code, 409)
        changed = api.update_social_link(self.person.id, second.id, SocialLinkUpdate(url='github.com/person', label='Code'), self.session)
        self.assertEqual((changed.platform, changed.handle, changed.brand_domain), ('github', 'person', 'github.com'))
        self.assertEqual(changed.label, 'Code')
        self.assertEqual(api.get_social_links(self.person.id, self.session)[0].id, first.id)

    def test_unknown_brand_schedules_after_commit_without_fetch(self):
        from fastapi import BackgroundTasks
        from app.api.person import extensions as api
        background = BackgroundTasks()
        link = api.add_social_link(self.person.id, SocialLinkCreate(url='example.com/me'), self.session, background)
        self.assertEqual(link.brand.status, 'pending')
        self.assertEqual(len(background.tasks), 1)
        self.assertEqual(background.tasks[0].args, ('example.com',))
        self.assertIsNotNone(self.session.get(PersonSocialLink, link.id))
