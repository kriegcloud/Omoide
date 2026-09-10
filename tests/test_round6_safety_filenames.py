"""Filename fixtures use real files and exact URL-segment encoding from the UI."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

_CONFIG = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG.name)

import httpx
from fastapi import FastAPI, Request
from sqlmodel import SQLModel, Session, create_engine, select
from app.config import settings
from app.models import Blacklist, Media, Person, PersonMediaLink, ProcessingTask
from app.schemas.media import MediaMoveRequest, MediaRenameRequest, MediaBulkMoveRequest

media_api = importlib.import_module("app.api.media")
from app.tasks.scan import _walk_media_candidates
exports_api = importlib.import_module("app.api.exports")
utils = importlib.import_module("app.utils")
NAMES = [f"symbol{symbol}name.jpg" for symbol in '#&;%_+[]\'"'] + [
    "family 👩‍👩‍👧‍👦.jpg", "trailing.jpg ", ".hidden.jpg", "l" * 240 + ".jpg", " ",
]


def encode_file_path(path):
    # encodeURIComponent applied separately to slash-delimited path segments.
    return "/".join(quote(segment, safe="~()*!.'-") for segment in str(path).split("/"))


class FilenameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.roots = patch.object(type(settings.general), "resolved_media_dirs", return_value=[(self.root, False)])
        self.roots.start()
        self.addCleanup(self.roots.stop)
        self.rows = []
        for name in NAMES:
            path = self.source / name
            path.write_bytes(b"fixture media")
            row = Media(path=str(path), filename=name, size=13)
            self.session.add(row)
            self.rows.append(row)
        self.session.commit()

    def test_move_and_bulk_move_preserve_all_filename_bytes(self):
        for row in self.rows:
            with self.subTest(name=row.filename):
                old_name = row.filename
                media_api.move_media(row.id, MediaMoveRequest(destination_dir=str(self.destination)), self.session)
                self.assertEqual(Path(row.path).name, old_name)
                self.assertEqual(Path(row.path).read_bytes(), b"fixture media")
        result = media_api.bulk_move_media(MediaBulkMoveRequest(media_ids=[r.id for r in self.rows], destination_dir=str(self.source)), self.session)
        self.assertEqual(set(result.moved_ids), {r.id for r in self.rows})
        self.assertEqual(result.skipped, [])
        self.assertEqual({p.name for p in self.source.iterdir()}, set(NAMES))

    def test_rename_preserves_trailing_space_and_single_space(self):
        for name in NAMES:
            with self.subTest(name=name):
                folder = self.root / str(NAMES.index(name))
                folder.mkdir()
                source = folder / "original"  # no implicit extension to append
                source.write_bytes(b"rename fixture")
                row = Media(path=str(source), filename=source.name, size=14)
                self.session.add(row)
                self.session.commit()
                renamed = media_api.rename_media(row.id, MediaRenameRequest(filename=name), self.session)
                target = Path(renamed.path)
                self.assertEqual(target.name, name)
                self.assertEqual(renamed.filename, name)
                self.assertEqual(target.read_bytes(), b"rename fixture")

    def test_folder_prefix_and_listing_treat_symbols_literally(self):
        for index, name in enumerate(NAMES):
            folder = self.root / name
            folder.mkdir()
            good = Media(path=str(folder / "good.jpg"), filename="good.jpg", size=1)
            wrong = Media(path=str(self.root / (name + "other") / "bad.jpg"), filename="bad.jpg", size=1)
            self.session.add_all([good, wrong])
            self.session.commit()
            with self.subTest(folder=name):
                clause = media_api._folder_prefix_clause(Media.path, str(folder))
                self.assertEqual(self.session.exec(select(Media.id).where(clause)).all(), [good.id])
                listing = media_api.list_media_folders(parent=name, preview_limit=0, session=self.session)
                self.assertEqual(listing.direct_media_count, 1)
        # LIKE wildcard substitution must not expand an underscore or percent.
        for literal, lookalike in (("under_score", "underXscore"), ("percent%name", "percentXYZname")):
            self.session.add_all([Media(path=f"{self.root}/{n}/x.jpg", filename="x.jpg", size=1) for n in (literal, lookalike)])
            self.session.commit()
            rows = self.session.exec(select(Media).where(media_api._folder_prefix_clause(Media.path, f"{self.root}/{literal}"))).all()
            self.assertEqual([r.path for r in rows], [f"{self.root}/{literal}/x.jpg"])

    def test_person_json_and_text_exports_preserve_names(self):
        person = Person(name="Fixture", appearance_count=len(self.rows))
        self.session.add(person)
        self.session.flush()
        self.session.add_all([PersonMediaLink(person_id=person.id, media_id=r.id) for r in self.rows])
        self.session.commit()
        paths = {r.path for r in self.rows}
        result = exports_api.export_person_paths(person.id, self.session)
        self.assertEqual({item.container_path for item in result.items}, paths)
        self.assertEqual(set(exports_api.export_person_paths_text(person.id, host_paths=False, session=self.session).splitlines()), paths)

    def test_open_commands_pass_paths_as_single_arguments(self):
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 123)})
        with patch.object(settings.general, "is_docker", False), patch.object(media_api.sys, "platform", "linux"), patch.object(media_api.subprocess, "Popen") as popen:
            for row in self.rows:
                with self.subTest(name=row.filename):
                    media_api.open_media_file(row.id, request, self.session)
                    self.assertEqual(popen.call_args.args[0], ["xdg-open", row.path])
                    self.assertFalse(popen.call_args.kwargs.get("shell", False))
                    media_api.open_media_folder(row.id, request, self.session)
                    self.assertEqual(popen.call_args.args[0], ["xdg-open", str(self.source)])
                    self.assertFalse(popen.call_args.kwargs.get("shell", False))

    def test_ffmpeg_thumbnail_arguments_preserve_symbols(self):
        thumb = self.root / "thumbs"
        thumb.mkdir()
        def render(args, **kwargs):
            self.assertIsInstance(args, list)
            self.assertFalse(kwargs.get("shell", False))
            Path(args[-1]).write_bytes(b"thumbnail")
        with patch.object(utils, "get_thumb_folder", return_value=thumb), patch.object(utils, "get_ffmpeg_accel_config", return_value=SimpleNamespace(hwaccel_args=[])), patch.object(utils, "run_silent", side_effect=render) as command, patch.object(type(settings.general), "thumb_dir", new_callable=lambda: property(lambda self: thumb)):
            for row in self.rows:
                # Put the fixture name in a directory too, so even a 244-byte
                # basename can be tested without exceeding NAME_MAX.
                path = self.root / row.filename / "video.mp4"
                video = Media(id=row.id, path=str(path), filename="video.mp4", size=1)
                result, error = utils.generate_thumbnail(video)
                self.assertIsNone(error)
                args = command.call_args.args[0]
                self.assertEqual(args[args.index("-i") + 1], str(path))

    def test_scan_discovery_preserves_names_with_supported_extensions(self):
        candidates = set(_walk_media_candidates([self.source], frozenset({".jpg"}), skip_thumbnails=False))
        expected = {Path(r.path) for r in self.rows if Path(r.path).suffix.lower() == ".jpg"}
        self.assertEqual(candidates, expected)


    def test_blacklist_normalization_and_scan_skip_for_fixture_matrix(self):
        from app import database
        from app.tasks import scan
        # File-backed SQLite lets the scanner open its normal independent sessions.
        engine = create_engine(f"sqlite:///{self.root / 'scan.sqlite'}")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for name in NAMES:
                raw = str(self.source / "nested" / ".." / name)
                self.assertEqual(scan._scan_path_key(raw), str(self.source / name))
                session.add(Blacklist(path=raw))
            task = ProcessingTask(task_type="scan", status="pending")
            session.add(task)
            session.commit()
            task_id = task.id
        with patch.object(database, "engine", engine), patch.object(scan, "process_file") as process:
            scan.run_scan(task_id)
        process.assert_not_called()
        with Session(engine) as session:
            self.assertEqual(session.exec(select(Media)).all(), [])


class FilenameServingTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoded_original_and_thumbnail_urls_serve_exact_file(self):
        from app.main import serve_original_media, serve_thumbnail
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = FastAPI()
            app.add_api_route("/originals/{file_path:path}", serve_original_media)
            app.add_api_route("/thumbnails/{file_path:path}", serve_thumbnail)
            with patch.object(type(settings.general), "resolved_media_dirs", return_value=[(root, False)]), patch.object(type(settings.general), "thumb_dir", new_callable=lambda: property(lambda self: root)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    for name in NAMES:
                        path = root / name
                        content = name.encode("utf-8")
                        path.write_bytes(content)
                        for url in ("/originals/" + encode_file_path(path), "/thumbnails/" + encode_file_path(name)):
                            with self.subTest(name=name, route=url.split('/')[1]):
                                response = await client.get(url)
                                self.assertEqual(response.status_code, 200, response.text)
                                self.assertEqual(response.content, content)
