"""Real ASGI file responses, without importing app startup or binding a port."""
import ast
import asyncio
import gzip
import hashlib
import mimetypes
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.staticfiles import StaticFiles, NotModifiedResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from anyio import to_thread

ROOT = Path(__file__).resolve().parents[1]


def runtime(settings):
    # Execute the actual owned route/helper definitions with isolated settings;
    # importing main would initialize personal configuration and model services.
    tree = ast.parse((ROOT / 'app/main.py').read_text())
    names = {'serve_thumbnail', 'TransportStaticFiles', '_accepts_encoding',
             'TextGZipResponder', 'TextGZipMiddleware', '_is_compressible_text'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    namespace = dict(globals(), settings=settings)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app/main.py', 'exec'), namespace)
    return namespace


class HttpAssetCachingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # AnyIO's worker wakeup stalls in this restricted sandbox. Keep real
        # ASGI/StaticFiles/FileResponse behavior but run fixture-only disk calls
        # inline; this suite makes no thread/concurrency claims.
        async def fixture_run_sync(func, *args, **kwargs):
            return func(*args)
        self.enterContext(patch('anyio.to_thread.run_sync', fixture_run_sync))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'assets').mkdir()
        self.thumb = self.root / 'thumb.jpg'
        self.thumb.write_bytes(b'first thumbnail')
        (self.root / 'index.html').write_text('<html>fixture</html>')
        self.js = self.root / 'assets/app-Ab12_cd3.js'
        self.js.write_bytes(b'export const fixture = "hello";\n' * 1000)
        Path(str(self.js) + '.gz').write_bytes(gzip.compress(self.js.read_bytes(), mtime=0))
        self.ns = runtime(SimpleNamespace(general=SimpleNamespace(thumb_dir=self.root)))
        app = FastAPI()
        app.add_api_route('/thumbnails/{file_path:path}', self.ns['serve_thumbnail'], methods=['GET', 'HEAD'])
        static = self.ns.get('TransportStaticFiles', StaticFiles)
        app.mount('/static', static(directory=self.root, html=True))
        @app.get('/api/fixture')
        async def fixture():
            return {'text': 'compressible fixture ' * 1000}
        @app.get('/video')
        async def video():
            return Response(b'video' * 1000, media_type='video/mp4')
        middleware = self.ns.get('TextGZipMiddleware')
        if middleware:
            app.add_middleware(middleware, minimum_size=1000)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture')
        self.addAsyncCleanup(self.client.aclose)

    async def test_thumbnail_matching_etag_returns_bodyless_304(self):
        first = await self.client.get('/thumbnails/thumb.jpg')
        self.assertEqual(first.status_code, 200)
        cached = await self.client.get('/thumbnails/thumb.jpg', headers={'If-None-Match': 'W/' + first.headers['etag']})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.content, b'')
        self.assertEqual(cached.headers['cache-control'], 'private, no-cache')

    async def test_thumbnail_change_invalidates_old_validator_even_with_version_query(self):
        first = await self.client.get('/thumbnails/thumb.jpg?v=old')
        self.thumb.write_bytes(b'changed thumbnail content')
        changed = await self.client.get('/thumbnails/thumb.jpg?v=old', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.headers['etag'], first.headers['etag'])
        self.assertEqual(changed.content, self.thumb.read_bytes())
        self.assertEqual(changed.headers.get('cache-control'), 'private, no-cache')

    async def test_etag_takes_precedence_over_modified_since_and_traversal_fails(self):
        first = await self.client.get('/thumbnails/thumb.jpg')
        response = await self.client.get('/thumbnails/thumb.jpg', headers={'If-None-Match': '"stale"', 'If-Modified-Since': first.headers['last-modified']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get('/thumbnails/%2e%2e/secret')).status_code, 404)

    async def test_hashed_assets_are_immutable_and_retained_chunks_still_resolve(self):
        old = self.root / 'assets/old-Zy98_xW1.js'
        old.write_bytes(b'old lazy chunk')
        for name in [self.js.name, old.name]:
            response = await self.client.get('/static/assets/' + name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get('cache-control'), 'private, max-age=31536000, immutable')
            cached = await self.client.get('/static/assets/' + name, headers={'If-None-Match': response.headers['etag']})
            self.assertEqual(cached.status_code, 304)

    async def test_index_and_unhashed_assets_revalidate(self):
        (self.root / 'assets/plain.js').write_text('plain')
        for path in ['/static/index.html', '/static/assets/plain.js']:
            response = await self.client.get(path)
            self.assertEqual(response.headers.get('cache-control'), 'private, no-cache')

    async def test_precompressed_asset_negotiation_and_representation_validators(self):
        encoded = await self.client.get('/static/assets/' + self.js.name, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(encoded.headers.get('content-encoding'), 'gzip')
        self.assertEqual(encoded.content, self.js.read_bytes())
        self.assertLess(int(encoded.headers['content-length']), len(encoded.content))
        self.assertIn('Accept-Encoding', encoded.headers['vary'])
        identity = await self.client.get('/static/assets/' + self.js.name, headers={'Accept-Encoding': 'gzip;q=0, identity'})
        self.assertNotIn('content-encoding', identity.headers)
        self.assertNotEqual(identity.headers['etag'], encoded.headers['etag'])
        cached = await self.client.get('/static/assets/' + self.js.name, headers={'Accept-Encoding': 'gzip', 'If-None-Match': encoded.headers['etag']})
        self.assertEqual(cached.status_code, 304)
        self.assertIn('Accept-Encoding', cached.headers['vary'])

    async def test_identity_json_still_varies_on_encoding(self):
        response = await self.client.get('/api/fixture', headers={'Accept-Encoding': 'gzip;q=0, *;q=1'})
        self.assertNotIn('content-encoding', response.headers)
        self.assertIn('Accept-Encoding', response.headers.get('vary', ''))

    async def test_equal_size_atomic_thumbnail_replacement_with_preserved_mtime(self):
        first = await self.client.get('/thumbnails/thumb.jpg')
        before = self.thumb.stat()
        replacement = self.root / 'replacement.jpg'
        replacement.write_bytes(b'other thumbnail')
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(self.thumb)
        response = await self.client.get('/thumbnails/thumb.jpg', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers['etag'], first.headers['etag'])

    async def test_ranges_use_identity_bytes_and_head_has_no_body(self):
        response = await self.client.get('/static/assets/' + self.js.name, headers={'Accept-Encoding': 'gzip', 'Range': 'bytes=0-9'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.js.read_bytes()[:10])
        self.assertNotIn('content-encoding', response.headers)
        head = await self.client.head('/static/assets/' + self.js.name, headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(head.content, b'')
        self.assertEqual(head.headers['content-encoding'], 'gzip')

    async def test_json_compression_preserves_shape_and_skips_video(self):
        response = await self.client.get('/api/fixture', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.headers.get('content-encoding'), 'gzip')
        self.assertEqual(response.json(), {'text': 'compressible fixture ' * 1000})
        video = await self.client.get('/video', headers={'Accept-Encoding': 'gzip'})
        self.assertNotIn('content-encoding', video.headers)


class BuildTransportTests(unittest.TestCase):
    def test_vite_plugin_generates_deterministic_smaller_gzip_sidecars(self):
        import subprocess
        source = (ROOT / 'frontend/vite.config.mts').read_text()
        self.assertIn('// BEGIN transport precompression', source)
        plugin = source.split('// BEGIN transport precompression\n')[1].split('// END transport precompression')[0].replace(': Plugin', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            js = root / 'assets/app-Ab12_cd3.js'
            js.write_bytes(b'const example = "repeated value";\n' * 1000)
            image = root / 'assets/image-Ab12_cd3.jpg'
            image.write_bytes(b'jpg' * 1000)
            harness = '''import path from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
''' + plugin + '''
const plugin = precompressTextAssets();
await plugin.writeBundle({dir: process.argv[1]}, {'assets/app-Ab12_cd3.js': {}, 'assets/image-Ab12_cd3.jpg': {}});
'''
            subprocess.run(['node', '--input-type=module', '-e', harness, directory], check=True, capture_output=True)
            sidecar = Path(str(js) + '.gz')
            first = sidecar.read_bytes()
            self.assertEqual(gzip.decompress(first), js.read_bytes())
            self.assertLess(len(first), js.stat().st_size)
            subprocess.run(['node', '--input-type=module', '-e', harness, directory], check=True, capture_output=True)
            self.assertEqual(first, sidecar.read_bytes())
            self.assertFalse(Path(str(image) + '.gz').exists())

    def test_retention_preserves_outgoing_asset_and_sidecar_together(self):
        import json
        import subprocess
        import time
        script = (ROOT / 'scripts/deploy-frontend.sh').read_text()
        retirement = script.split('python3 - "$static_dir" <<\'PYTHON\'\n')[1].split('\nPYTHON')[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            original = root / 'assets/old-Ab12_cd3.js'
            sidecar = Path(str(original) + '.gz')
            for file in [original, sidecar]:
                file.write_bytes(b'old')
                os.utime(file, (1, 1))
            (root / '.omoide-current-assets.json').write_text(json.dumps(['assets/old-Ab12_cd3.js']))
            subprocess.run([__import__('sys').executable, '-c', retirement, directory], check=True)
            self.assertGreater(original.stat().st_mtime, time.time() - 10)
            self.assertGreater(sidecar.stat().st_mtime, time.time() - 10)
