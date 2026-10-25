"""Public API visibility, relationship and validation regression contracts."""
import importlib
import os
import tempfile
import unittest
from datetime import datetime
from enum import Enum
from unittest.mock import patch

_CONFIG = tempfile.TemporaryDirectory(prefix="omoide-api-domain-tests-")
os.environ["XDG_CONFIG_HOME"] = _CONFIG.name

import app.api  # noqa: E402,F401
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Enum as SqlEnum, column, func  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import ExifData, Face, Media, MediaTagLink, Person, PersonTagLink, Tag  # noqa: E402
from app.pagination import decode_cursor, encode_cursor  # noqa: E402


class ApiDomainContracts(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.enterContext(patch.object(settings.general, "write_location_to_files", False))
        app = FastAPI()
        for name, prefix in [("tags", "/api/tags"), ("person", "/api/person"),
                             ("media", "/api/media"), ("face", "/api/faces"), ("search", "/api/search")]:
            app.include_router(importlib.import_module("app.api." + name).router, prefix=prefix)
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def add(self, row):
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def media(self, path, **kwargs):
        return self.add(Media(path=path, filename="fixture.jpg", size=1, **kwargs))

    def person(self, name):
        return self.add(Person(name=name, appearance_count=0))

    def face(self, media, person=None):
        return self.add(Face(media_id=media.id, person_id=person.id if person else None, bbox=[0, 0, 10, 10]))

    def test_missing_tag_members_are_not_browsable(self):
        present = self.media("/fixture/present.jpg")
        missing = self.media("/fixture/missing.jpg", missing_since=datetime.now())
        tag = self.add(Tag(name="fixture"))
        self.session.add_all([MediaTagLink(media_id=media.id, tag_id=tag.id) for media in (present, missing)])
        self.session.commit()
        for url in ["/api/tags/", f"/api/tags/{tag.id}"]:
            with self.subTest(url=url):
                data = self.client.get(url).json()
                rows = data["items"][0]["media"] if "items" in data else data["media"]
                self.assertEqual([row["id"] for row in rows], [present.id])
        self.session.flush()
        self.assertEqual(set(self.session.exec(select(MediaTagLink.media_id)).all()), {present.id, missing.id})
        self.assertEqual({media.id for media in self.session.get(Tag, tag.id).media}, {present.id, missing.id})

    def test_tag_projection_preserves_pagination_and_person_metadata(self):
        person = self.person("Fixture")
        tags = [self.add(Tag(name=name)) for name in ("first", "second")]
        self.session.add(PersonTagLink(person_id=person.id, tag_id=tags[1].id))
        self.session.commit()
        first = self.client.get("/api/tags/", params={"limit": 1}).json()
        second = self.client.get("/api/tags/", params={"limit": 1, "cursor": first["next_cursor"]}).json()
        self.assertEqual([row["id"] for row in first["items"] + second["items"]], [tags[1].id, tags[0].id])
        self.assertEqual(first["items"][0]["persons"][0]["id"], person.id)

    def test_duplicate_person_tag_is_idempotent(self):
        person = self.person("Fixture")
        tag = self.add(Tag(name="fixture"))
        url = f"/api/tags/person/{person.id}/{tag.id}"
        for _ in range(3):
            self.assertEqual(self.client.post(url).status_code, 204)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(PersonTagLink)), 1)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.post(url).status_code, 204)
        self.assertEqual(self.client.post(f"/api/tags/person/999/{tag.id}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/tags/person/{person.id}/999").status_code, 404)

    def test_profile_face_must_belong_to_target_person(self):
        owner, target = self.person("Owner"), self.person("Target")
        media = self.media("/fixture/profile.jpg")
        own, foreign, orphan = self.face(media, target), self.face(media, owner), self.face(media)
        target.profile_face_id = own.id
        self.session.add(target)
        self.session.commit()
        for face_id in (foreign.id, orphan.id, 999, -1, 2**64):
            for method in ("PATCH", "POST"):
                with self.subTest(method=method, face_id=face_id):
                    url = f"/api/person/{target.id}"
                    kwargs = {"json": {"profile_face_id": face_id}}
                    if method == "POST":
                        url += "/profile_face"
                        kwargs = {"params": {"face_id": face_id}}
                    response = self.client.request(method, url, **kwargs)
                    self.assertIn(response.status_code, (400, 404, 422), response.text)
                    self.session.refresh(target)
                    self.assertEqual(target.profile_face_id, own.id)
        self.session.refresh(foreign)
        self.assertEqual(foreign.person_id, owner.id)

    def test_profile_face_own_face_and_null_clear_on_both_routes(self):
        person = self.person("Fixture")
        face = self.face(self.media("/fixture/own.jpg"), person)
        patch_url = f"/api/person/{person.id}"
        post_url = patch_url + "/profile_face"
        for set_response, clear in (
            (self.client.patch(patch_url, json={"profile_face_id": face.id}),
             lambda: self.client.patch(patch_url, json={"profile_face_id": None})),
            (self.client.post(post_url, params={"face_id": face.id}), lambda: self.client.post(post_url)),
        ):
            self.assertEqual(set_response.status_code, 200, set_response.text)
            response = clear()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["profile_face_id"])

    def test_invalid_list_arguments_are_client_errors(self):
        urls = ["/api/person/?limit=0", "/api/media/?cursor=WyJuZXdlc3QiLCBbMV0sIDFd"]
        for path, maximum in [("/api/person/", 500), ("/api/person/1/faces", 200),
                              ("/api/person/1/media-appearances", 200), ("/api/faces/orphans", 200),
                              ("/api/search/combined", 500), ("/api/search/scenes", 500)]:
            urls.extend(f"{path}?limit={limit}" for limit in (0, -1, maximum + 1))
        for url in urls:
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (400, 422))

    def test_cursor_key_types_reject_booleans_nonfinite_and_container_values(self):
        for value in ([1], {"bad": "value"}, None, True, "not-a-date", 5):
            with self.subTest(date_key=value):
                cursor = encode_cursor("newest", [value, 1])
                self.assertEqual(self.client.get("/api/media/", params={"cursor": cursor}).status_code, 400)
        keys = [(Media.laplacian_score, True), (Media.id, True)]
        for values in ([True, 1], [float("nan"), 1], [float("inf"), 1], [0, True], [0, 1.5], [0, 2**64]):
            with self.subTest(values=values), self.assertRaises(HTTPException) as error:
                decode_cursor(encode_cursor("", values), "", 2, keys=keys)
            self.assertEqual(error.exception.status_code, 400)

    def test_valid_cursor_roundtrips_support_zero_float_enum_datetime_and_autostring(self):
        class Mode(str, Enum):
            FIRST = "first"
            SECOND = "second"
        moment = datetime(2026, 1, 2, 3, 4, 5)
        keys = [(Media.created_at, True), (Media.path, True), (Media.laplacian_score, True),
                (Media.id, True), (column("mode", SqlEnum(Mode)), True)]
        values = [moment, "/fixture/literal%_.jpg", 0.0, 0, Mode.FIRST]
        self.assertEqual(decode_cursor(encode_cursor("fixture", values), "fixture", len(keys), keys=keys), values)
        self.assertEqual(decode_cursor(encode_cursor("", [0, 1]), "", 2,
                                       keys=[(Media.laplacian_score, True), (Media.id, True)]), [0, 1])

    def test_person_name_and_zero_appearance_sorts_paginate_without_losing_rows(self):
        expected = [self.person(name).id for name in ("Alpha", "Beta", "")]
        for sort in ("name_asc", "appearances_desc", "views_desc", "favorites"):
            cursor, found = None, []
            for _ in range(4):
                params = {"limit": 1, "sort": sort}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.get("/api/person/", params=params)
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                found.extend(row["id"] for row in page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            self.assertEqual(set(found), set(expected))
            self.assertEqual(len(found), len(expected))
            if sort == "name_asc":
                self.assertEqual(found, expected)

    def test_invalid_geographic_coordinates_are_rejected_without_changing_state_or_file(self):
        media = self.media("/fixture/location.jpg")
        exif = self.add(ExifData(media_id=media.id, lat=10, lon=20, city="Prior city", country="Prior country"))
        for latitude, longitude in [(999, 0), (-91, 0), (0, 999), (0, -181), ("NaN", 0), (0, "Infinity")]:
            with self.subTest(latitude=latitude, longitude=longitude), \
                 patch.object(settings.general, "write_location_to_files", True), \
                 patch("app.api.media.core.update_exif_gps") as writer:
                response = self.client.patch(f"/api/media/{media.id}/geolocation",
                                             json={"latitude": latitude, "longitude": longitude})
                self.assertEqual(response.status_code, 422, response.text)
                writer.assert_not_called()
                self.session.refresh(exif)
                self.assertEqual((exif.lat, exif.lon, exif.city, exif.country), (10, 20, "Prior city", "Prior country"))

    def test_valid_geographic_boundaries_and_zero_are_preserved(self):
        media = self.media("/fixture/location.jpg")
        for latitude, longitude in [(-90, -180), (90, 180), (0, 0), (-30.25, 12.75)]:
            with self.subTest(latitude=latitude, longitude=longitude), patch("app.api.media.core.update_exif_gps") as writer:
                response = self.client.patch(f"/api/media/{media.id}/geolocation",
                                             json={"latitude": latitude, "longitude": longitude})
                self.assertEqual(response.status_code, 200, response.text)
                writer.assert_not_called()
                exif = self.session.exec(select(ExifData).where(ExifData.media_id == media.id)).one()
                self.session.refresh(exif)
                self.assertEqual((exif.lat, exif.lon), (latitude, longitude))

    def test_nonfinite_raw_json_returns_serializable_validation_errors(self):
        from fastapi.exceptions import RequestValidationError
        from app.main import app as production

        self.client.app.add_exception_handler(RequestValidationError,
                                              production.exception_handlers[RequestValidationError])
        media = self.media("/fixture/nonfinite.jpg")
        with patch("app.api.media.core.update_exif_gps") as writer:
            for token in ("NaN", "Infinity", "-Infinity", "1e999"):
                with self.subTest(token=token):
                    response = self.client.patch(f"/api/media/{media.id}/geolocation",
                                                 content='{"latitude":' + token + ',"longitude":0}',
                                                 headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"][0]["loc"], ["body", "latitude"])
            writer.assert_not_called()
        self.assertEqual(self.session.exec(select(ExifData)).all(), [])

    def test_validation_handler_preserves_ordinary_error_payload(self):
        from fastapi.exceptions import RequestValidationError
        from app.main import app as production

        media = self.media("/fixture/invalid.jpg")
        url = f"/api/media/{media.id}/geolocation"
        body = {"latitude": "not-a-number", "longitude": 0}
        ordinary = self.client.patch(url, json=body)
        self.assertEqual(ordinary.status_code, 422)
        # A new client/app is needed because Starlette has built the first stack.
        app = FastAPI(exception_handlers={RequestValidationError:
                      production.exception_handlers[RequestValidationError]})
        app.include_router(importlib.import_module("app.api.media").router, prefix="/api/media")
        app.dependency_overrides[get_session] = lambda: self.session
        with TestClient(app) as client:
            handled = client.patch(url, json=body)
        self.assertEqual(handled.status_code, ordinary.status_code)
        self.assertEqual(handled.json(), ordinary.json())
