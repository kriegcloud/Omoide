"""Offline security and resolution contract tests; never contact external hosts."""
import io
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.social_brands import (
    MAX_BYTES, FetchResult, SafeFetcher, discover_icons, resolve_icon,
    sanitize_image, validate_public_url,
)

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


class BrandSecurityTests(unittest.TestCase):
    def test_ssrf_private_and_mixed_dns_rejected(self):
        for host in ('127.0.0.1', '10.1.1.1', '169.254.169.254', '[::1]', '[::ffff:127.0.0.1]'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_public_url('https://' + host)
        with patch('socket.getaddrinfo', return_value=PUBLIC + [(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                validate_public_url('https://example.com')

    def test_bad_schemes_credentials_ports(self):
        for url in ('file:///etc/passwd', 'ftp://example.com', 'https://u:p@example.com', 'https://example.com:22'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_url(url)

    def test_redirect_to_private_is_revalidated(self):
        with patch('socket.getaddrinfo', return_value=PUBLIC), patch.object(SafeFetcher, '_request', return_value=(302, {'location': 'http://127.0.0.1/'}, b'')):
            with self.assertRaises(ValueError):
                SafeFetcher().fetch('https://example.com')

    def test_wrong_content_type_and_oversize(self):
        with self.assertRaises(ValueError):
            sanitize_image(b'x' * (MAX_BYTES + 1), 'image/png')
        with self.assertRaises(ValueError):
            sanitize_image(b'<html/>', 'text/html')

    def test_svg_allowlist_removes_scripts_external_refs_and_events(self):
        data, kind = sanitize_image(b'<svg xmlns="http://www.w3.org/2000/svg" onload="bad()"><script>bad()</script><foreignObject/><image href="https://evil/a"/><path d="M0 0" onclick="bad()"/></svg>', 'image/svg+xml')
        self.assertEqual(kind, 'image/svg+xml')
        for unsafe in (b'script', b'foreignObject', b'https://evil', b'onload', b'onclick'):
            self.assertNotIn(unsafe, data)
        self.assertIn(b'path', data)

    def test_png_is_decoded_and_reencoded(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (4, 4)).save(stream, 'PNG')
        data, kind = sanitize_image(stream.getvalue(), 'image/png')
        self.assertEqual(kind, 'image/png')
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_largest_square_icon_first(self):
        html = '<link rel="icon" sizes="32x32" href="/small.png"><link rel="apple-touch-icon" sizes="180x180" href="/large.png"><link rel="icon" sizes="900x20" href="/wide.png">'
        icons, manifests = discover_icons(html, 'https://example.com/')
        self.assertEqual(icons[0][1], 'https://example.com/large.png')
        self.assertEqual(manifests, [])

    def test_registry_precedes_fetch_and_fallback_is_root_only(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('/'):
                return FetchResult(url, 'text/html', b'<link rel="icon" href="/favicon.svg">')
            return FetchResult(url, 'image/svg+xml', b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>')
        result = resolve_icon('instagram.com', fetch=fetch)
        self.assertEqual(result['source'], 'simple-icons')
        self.assertEqual(calls, [])
        result = resolve_icon('unknown-example.test', fetch=fetch)
        self.assertEqual(result['source'], 'site-icon')
        self.assertEqual(calls[0], 'https://unknown-example.test/')
        self.assertNotIn('/person', ''.join(calls))

    def test_failed_fetch_marks_needs_icon(self):
        def fail(url):
            raise ValueError('blocked')
        result = resolve_icon('unknown-example.test', fetch=fail)
        self.assertEqual(result['status'], 'needs_icon')
        self.assertIn('blocked', result['error'])

    def test_www_favicon_is_tried_when_apex_serves_a_login_wall(self):
        ico = io.BytesIO()
        from PIL import Image
        Image.new('RGBA', (32, 32), (10, 102, 194, 255)).save(ico, format='ICO')
        def fetch(url):
            if url == 'https://www.walled.test/favicon.ico':
                return FetchResult(url, 'image/x-icon', ico.getvalue())
            return FetchResult(url, 'text/html', b'<html>sign in</html>')
        result = resolve_icon('walled.test', fetch=fetch, browser=lambda _d: [])
        self.assertEqual(result['source'], 'site-icon')
        self.assertEqual(result['content_type'], 'image/png')

    def test_browser_fallback_runs_only_after_http_finds_nothing(self):
        seen = []
        def fetch(url):
            if url == 'https://cdn.spa.test/icon-192.png':
                buf = io.BytesIO()
                from PIL import Image
                Image.new('RGB', (192, 192), 'red').save(buf, format='PNG')
                return FetchResult(url, 'image/png', buf.getvalue())
            raise ValueError('not here')
        def browser(domain):
            seen.append(domain)
            return [(192, 'https://cdn.spa.test/icon-192.png')]
        result = resolve_icon('spa.test', fetch=fetch, browser=browser)
        self.assertEqual(seen, ['spa.test'])
        self.assertEqual(result['source'], 'site-icon-browser')
        self.assertEqual(result['status'], 'ready')

    def test_browser_failure_is_reported_not_raised(self):
        def fail(url):
            raise ValueError('blocked')
        def browser(_domain):
            raise ValueError('no chromium')
        result = resolve_icon('spa.test', fetch=fail, browser=browser)
        self.assertEqual(result['status'], 'needs_icon')
        self.assertIn('browser: no chromium', result['error'])

    def test_injected_fetch_never_launches_a_real_browser(self):
        with patch('app.services.social_brands.discover_icons_with_browser') as real:
            resolve_icon('unknown-example.test', fetch=lambda url: (_ for _ in ()).throw(ValueError('x')))
        real.assert_not_called()

    def test_redirect_cap(self):
        with patch('socket.getaddrinfo', return_value=PUBLIC), patch.object(SafeFetcher, '_request', return_value=(302, {'location': '/again'}, b'')) as request:
            with self.assertRaisesRegex(ValueError, 'redirect'):
                SafeFetcher().fetch('https://example.com')
            self.assertEqual(request.call_count, 4)

    def test_fetch_enforces_size_and_type_even_with_mock_transport(self):
        for headers, body in (({'content-type': 'image/png'}, b'x' * (MAX_BYTES + 1)), ({'content-type': 'text/plain'}, b'x')):
            with patch('socket.getaddrinfo', return_value=PUBLIC), patch.object(SafeFetcher, '_request', return_value=(200, headers, body)):
                with self.assertRaises(ValueError):
                    SafeFetcher().fetch('https://example.com')

    def test_manifest_largest_icon_precedes_html_icon(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('/'):
                return FetchResult(url, 'text/html', b'<link rel="icon" sizes="16x16" href="/small.png"><link rel="manifest" href="/manifest.json">')
            if url.endswith('.json'):
                return FetchResult(url, 'application/manifest+json', b'{"icons":[{"src":"/large.svg","sizes":"512x512"}]}')
            return FetchResult(url, 'image/svg+xml', b'<svg/>')
        self.assertEqual(resolve_icon('unknown-example.test', fetch=fetch)['status'], 'ready')
        self.assertEqual(calls[-1], 'https://unknown-example.test/large.svg')

    def test_metadata_domain_map_avoids_http(self):
        with patch('app.services.social_brands.SafeFetcher.fetch', side_effect=AssertionError('network')):
            result = resolve_icon('docker.com')
        self.assertEqual(result['source'], 'simple-icons')
        self.assertEqual(result['simple_icons_slug'], 'docker')


class BrandApiTests(unittest.TestCase):
    def setUp(self):
        from sqlmodel import SQLModel, Session, create_engine
        from app.models import SocialBrand
        self.engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(SocialBrand(domain='example.com', display_name='Example', status='needs_icon'))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_get_unknown_retry_and_manual_override(self):
        from fastapi import BackgroundTasks, HTTPException
        from app.api.social_brands import get_brand, retry_icon
        with self.assertRaises(HTTPException) as error:
            get_brand('missing.example', self.session)
        self.assertEqual(error.exception.status_code, 404)
        tasks = BackgroundTasks()
        brand = retry_icon('example.com', tasks, self.session)
        self.assertEqual(brand.status, 'pending')
        self.assertEqual(len(tasks.tasks), 1)
        brand.source = 'manual'
        self.session.add(brand)
        self.session.commit()
        tasks = BackgroundTasks()
        self.assertEqual(retry_icon('example.com', tasks, self.session).source, 'manual')
        self.assertEqual(tasks.tasks, [])

    def test_upload_and_serve_are_sanitized_and_confined(self):
        import asyncio
        from starlette.requests import Request
        from fastapi import HTTPException
        from app.api.social_brands import upload_icon, get_icon
        from app.config import settings
        async def receive():
            return {'type': 'http.request', 'body': b'<svg><script>alert(1)</script><path d="M0 0"/></svg>', 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'headers': [(b'content-type', b'image/svg+xml')]}, receive)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'social-brands' / 'safe.svg'
            path.parent.mkdir()
            def store(domain, data, kind):
                self.assertNotIn(b'script', data)
                path.write_bytes(data)
                return str(path)
            with patch('app.api.social_brands.store_icon', side_effect=store):
                result = asyncio.run(upload_icon('example.com', request, self.session))
            self.assertEqual(result.source, 'manual')
            self.assertIsNone(result.simple_icons_slug)
            # A DB path outside the thumbnail cache cannot be served.
            with self.assertRaises(HTTPException):
                get_icon('example.com', self.session)
            with patch.object(type(settings.general), 'thumb_dir', new_callable=lambda: property(lambda _: Path(directory))):
                response = get_icon('example.com', self.session)
                self.assertEqual(response.media_type, 'image/svg+xml')
                self.assertIn('sandbox', response.headers['content-security-policy'])

class BrandDeadlineTests(unittest.TestCase):
    def test_hard_deadline_interrupts_slow_headers(self):
        import threading
        import time
        stopped = threading.Event()
        class Socket:
            def settimeout(self, value): pass
            def shutdown(self, how): stopped.set()
            def close(self): stopped.set()
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self):
                stopped.wait(0.5)
                raise OSError('deadline closed socket')
            def close(self): pass
        fetcher = SafeFetcher()
        fetcher.deadline = time.monotonic() + 0.03
        started = time.monotonic()
        with patch('socket.create_connection', return_value=Socket()), patch('http.client.HTTPConnection', Connection):
            with self.assertRaises(OSError):
                fetcher._request('http://example.com/', 'example.com', 80, ['93.184.216.34'])
        self.assertTrue(stopped.is_set(), 'hard timer must interrupt a slow header stream')
        self.assertLess(time.monotonic() - started, 0.25, 'Header trickle must not outlive hard deadline')

    def test_background_commit_cannot_overwrite_manual_upload(self):
        from app.services.social_brands import commit_brand_result
        from sqlmodel import SQLModel, Session, create_engine
        from app.models import SocialBrand
        engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(SocialBrand(domain='example.com', display_name='Example', source='manual', icon_path='/manual.png', status='ready'))
            session.commit()
            commit_brand_result(session, 'example.com', {'source': 'site-icon', 'status': 'ready'}, '/background.png')
            brand = session.get(SocialBrand, 'example.com')
            self.assertEqual(brand.source, 'manual')
            self.assertEqual(brand.icon_path, '/manual.png')
        engine.dispose()
