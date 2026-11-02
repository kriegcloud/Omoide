"""Cached brand assets. HTTP transport pins public DNS results; no profile fetches.

When plain HTTP finds no icon (JavaScript-rendered sites, login walls), a
headless Chromium renders the brand domain's root page only, never a person's
URL. Every browser request is checked by validate_public_url, and the icon it
finds is downloaded through SafeFetcher like any other candidate.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import re
import shutil
import socket
import queue
import threading
import tempfile
import ssl
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 512 * 1024
TIMEOUT = 5
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/x-icon', 'image/vnd.microsoft.icon', 'image/svg+xml'}
DOCUMENT_TYPES = {'text/html', 'application/xhtml+xml', 'application/manifest+json', 'application/json'}


def validate_public_url(url: str, timeout: float = TIMEOUT) -> tuple[str, int, list[str]]:
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('Only public HTTP(S) URLs without credentials are allowed')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in {80, 443}:
        raise ValueError('Unsupported port')
    host = parsed.hostname.encode('idna').decode('ascii')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        answers = queue.Queue(maxsize=1)
        def resolve():
            try:
                answers.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except Exception as error:
                answers.put(error)
        # Daemon resolution avoids an unbounded OS DNS wait holding the job open.
        threading.Thread(target=resolve, daemon=True).start()
        try:
            rows = answers.get(timeout=timeout)
        except queue.Empty as error:
            raise ValueError('DNS resolution timed out') from error
        if isinstance(rows, Exception):
            raise ValueError('DNS resolution failed') from rows
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
    else:
        addresses = [str(literal)]
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Private, loopback, reserved and link-local addresses are blocked')
    return host, port, addresses


@dataclass(frozen=True)
class FetchResult:
    url: str
    content_type: str
    body: bytes


class SafeFetcher:
    """One bounded resolution session; revalidate every hop and bypass proxy env."""
    def __init__(self):
        self.deadline = time.monotonic() + TIMEOUT

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Icon resolution timed out')
        return remaining

    def _request(self, url, host, port, addresses):
        parsed = urlsplit(url)
        # Connect to the validated numeric address. TLS still verifies original hostname.
        connection = http.client.HTTPConnection(host, port, timeout=self._remaining())
        sock = socket.create_connection((addresses[0], port), timeout=self._remaining())
        def interrupt():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0, self.deadline - time.monotonic()), interrupt)
        timer.daemon = True
        timer.start()
        try:
            if parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
                sock.settimeout(self._remaining())
                sock.do_handshake()
            sock.settimeout(self._remaining())
            connection.sock = sock
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            connection.request('GET', path, headers={'Host': host, 'User-Agent': 'Omoide-BrandIcon/1', 'Accept-Encoding': 'identity'})
            sock.settimeout(self._remaining())
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if int(headers.get('content-length', '0')) > MAX_BYTES:
                raise ValueError('Icon response exceeds 512 KiB')
            if headers.get('content-encoding', 'identity').lower() not in {'', 'identity'}:
                raise ValueError('Compressed icon responses are not accepted')
            if response.status in {301, 302, 303, 307, 308}:
                return response.status, headers, b''
            kind = headers.get('content-type', '').split(';')[0].lower().strip()
            if kind not in IMAGE_TYPES | DOCUMENT_TYPES:
                raise ValueError('Unsupported icon response content type')
            chunks, size = [], 0
            while True:
                sock.settimeout(self._remaining())
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Icon response exceeds 512 KiB')
            return response.status, headers, b''.join(chunks)
        finally:
            timer.cancel()
            connection.close()
            sock.close()

    def fetch(self, url: str) -> FetchResult:
        for hop in range(4):
            self._remaining()
            host, port, addresses = validate_public_url(url, self._remaining())
            self._remaining()
            status, headers, body = self._request(url, host, port, addresses)
            if status in {301, 302, 303, 307, 308}:
                if hop == 3 or not headers.get('location'):
                    raise ValueError('Icon redirect limit exceeded')
                url = urljoin(url, headers['location'])
                continue
            if not 200 <= status < 300:
                raise ValueError(f'Icon HTTP status {status}')
            if len(body) > MAX_BYTES:
                raise ValueError('Icon response exceeds 512 KiB')
            kind = headers.get('content-type', '').split(';')[0].lower().strip()
            if kind not in IMAGE_TYPES | DOCUMENT_TYPES:
                raise ValueError('Unsupported icon response content type')
            return FetchResult(url, kind, body)
        raise ValueError('Icon redirect limit exceeded')


def sanitize_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    if len(data) > MAX_BYTES or content_type not in IMAGE_TYPES:
        raise ValueError('Invalid icon size or content type')
    if content_type == 'image/svg+xml':
        if b'<!' in data:
            raise ValueError('SVG declarations/entities are not allowed')
        root = ET.fromstring(data)
        if root.tag.split('}')[-1] != 'svg':
            raise ValueError('Invalid SVG root')
        tags = {'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'defs', 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'title', 'desc'}
        attributes = {'viewBox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'fill', 'fill-rule', 'opacity', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity', 'transform', 'offset', 'stop-color', 'stop-opacity', 'id', 'gradientUnits', 'gradientTransform'}
        def clean(element):
            for key, value in list(element.attrib.items()):
                if key not in attributes or re.search(r'url\s*\(|(?:https?|data|javascript):', value, re.I):
                    del element.attrib[key]
            for child in list(element):
                if child.tag.split('}')[-1] not in tags:
                    element.remove(child)
                else:
                    clean(child)
        clean(root)
        return ET.tostring(root, encoding='utf-8'), 'image/svg+xml'
    from PIL import Image
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 4_194_304:
                raise ValueError('Icon dimensions too large')
            image.thumbnail((512, 512))
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            return output.getvalue(), 'image/png'


def _square_size(raw):
    if raw == 'any':
        return 1024
    sizes = [int(w) for w, h in re.findall(r'(\d+)x(\d+)', raw) if w == h]
    return max(sizes, default=0)


class _IconParser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.icons, self.manifests = base, [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag != 'link' or not values.get('href'):
            return
        rels = set((values.get('rel') or '').lower().split())
        href = urljoin(self.base, values['href'])
        if rels & {'icon', 'apple-touch-icon', 'apple-touch-icon-precomposed', 'mask-icon'}:
            self.icons.append((_square_size(values.get('sizes') or ''), href))
        if 'manifest' in rels:
            self.manifests.append(href)


def discover_icons(html: str, base: str):
    parser = _IconParser(base)
    parser.feed(html)
    return sorted(parser.icons, reverse=True), parser.manifests


def _simple_brand(domain):
    from app.services.social_registry import brand_metadata
    metadata = brand_metadata(domain)
    if metadata.get('simple_icons_slug'):
        return metadata
    path = Path(__file__).with_name('social_icon_domains.json')
    mapping = json.loads(path.read_text()) if path.exists() else {}
    return mapping.get(domain)


ICON_RELS = {'icon', 'shortcut', 'apple-touch-icon', 'apple-touch-icon-precomposed', 'mask-icon'}
BROWSER_TIMEOUT = 15
BLOCKED_BROWSER_RESOURCES = {'media', 'font', 'websocket', 'eventsource', 'manifest'}


def _favicon_fallbacks(domain: str) -> list[str]:
    hosts = [domain] if domain.startswith('www.') else [domain, f'www.{domain}']
    return [f'https://{host}/favicon.ico' for host in hosts]


def discover_icons_with_browser(domain: str, timeout: float = BROWSER_TIMEOUT) -> list[tuple[int, str]]:
    """Render https://<domain>/ headlessly and return icon candidates from the live DOM."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ValueError('Browser fallback unavailable: playwright is not installed') from error
    executable = shutil.which('chromium') or shutil.which('chromium-browser')
    if not executable:
        raise ValueError('Browser fallback unavailable: no Chromium executable')
    root = f'https://{domain}/'
    validate_public_url(root)

    def guard(route):
        request = route.request
        if request.url.startswith('data:'):
            return route.continue_()
        if request.resource_type in BLOCKED_BROWSER_RESOURCES:
            return route.abort()
        try:
            validate_public_url(request.url, timeout=3)
        except ValueError:
            return route.abort()
        return route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable,
            headless=True,
            args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
        )
        try:
            context = browser.new_context(service_workers='block', accept_downloads=False)
            context.route('**/*', guard)
            page = context.new_page()
            page.goto(root, wait_until='domcontentloaded', timeout=timeout * 1000)
            page.wait_for_timeout(1500)
            links = page.eval_on_selector_all(
                'link[href]',
                'els => els.map(e => [e.getAttribute("rel") || "", e.getAttribute("sizes") || "", e.href])',
            )
            final_url = page.url
        finally:
            browser.close()
    icons = [
        (_square_size(sizes), href)
        for rel, sizes, href in links[:64]
        if set(str(rel).lower().split()) & ICON_RELS and str(href).startswith(('https://', 'http://'))
    ]
    return sorted(icons, reverse=True)[:12] + [(0, urljoin(final_url, '/favicon.ico'))]


def _first_usable_icon(candidates, fetch, errors):
    seen = set()
    for _, url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            result = fetch(url)
            return sanitize_image(result.body, result.content_type)
        except Exception as error:
            errors.append(str(error))
    return None


def resolve_icon(domain: str, *, fetch=None, browser=None) -> dict:
    """Pure orchestration with injectable guarded HTTP. No DB or file mutations."""
    metadata = _simple_brand(domain)
    if metadata:
        return {**metadata, 'source': 'simple-icons', 'status': 'ready', 'error': None}
    if '/' in domain or ':' in domain or '@' in domain or not domain:
        return {'status': 'needs_icon', 'error': 'Invalid brand domain'}
    if browser is None and fetch is None:
        browser = discover_icons_with_browser
    fetch = fetch or SafeFetcher().fetch
    candidates, errors = [], []
    root = f'https://{domain}/'
    try:
        page = fetch(root)
        if page.content_type in {'text/html', 'application/xhtml+xml'}:
            candidates, manifests = discover_icons(page.body.decode('utf-8', errors='replace'), page.url)
            for manifest in manifests[:2]:
                try:
                    result = fetch(manifest)
                    if result.content_type not in {'application/manifest+json', 'application/json'}:
                        raise ValueError('Invalid manifest type')
                    for icon in json.loads(result.body).get('icons', [])[:32]:
                        if isinstance(icon, dict) and isinstance(icon.get('src'), str):
                            candidates.append((_square_size(icon.get('sizes', '')), urljoin(result.url, icon['src'])))
                except Exception as error:
                    errors.append(str(error))
        else:
            raise ValueError('Domain root did not return HTML')
    except Exception as error:
        errors.append(str(error))
    candidates = sorted(candidates, reverse=True)[:12] + [(0, url) for url in _favicon_fallbacks(domain)]
    icon = _first_usable_icon(candidates, fetch, errors)
    if icon:
        return {'source': 'site-icon', 'status': 'ready', 'error': None, 'data': icon[0], 'content_type': icon[1]}
    if not browser:
        return {'status': 'needs_icon', 'error': '; '.join(errors)[-500:] or 'No usable icon found'}
    try:
        icon = _first_usable_icon(browser(domain), fetch, errors)
    except Exception as error:
        errors.append(f'browser: {error}')
        icon = None
    if icon:
        return {'source': 'site-icon-browser', 'status': 'ready', 'error': None, 'data': icon[0], 'content_type': icon[1]}
    return {'status': 'needs_icon', 'error': '; '.join(errors)[-500:] or 'No usable icon found'}


def store_icon(domain: str, data: bytes, kind: str) -> str:
    from app.config import settings
    directory = settings.general.thumb_dir / 'social-brands'
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(domain.encode() + data).hexdigest()
    target = directory / (digest + ('.svg' if kind == 'image/svg+xml' else '.png'))
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def resolve_brand(domain: str):
    """Background job after a successful link save. Keep manual assets authoritative."""
    from sqlmodel import Session
    from app.database import engine
    from app.models import SocialBrand
    with Session(engine) as session:
        brand = session.get(SocialBrand, domain)
        if not brand or brand.source == 'manual' or brand.status == 'ready':
            return
        known_slug = brand.simple_icons_slug
    try:
        result = {'source': 'simple-icons', 'status': 'ready', 'error': None} if known_slug else resolve_icon(domain)
        icon_path = store_icon(domain, result.pop('data'), result.pop('content_type')) if 'data' in result else None
    except Exception as error:
        result, icon_path = {'status': 'needs_icon', 'error': str(error)[:500]}, None
    with Session(engine) as session:
        commit_brand_result(session, domain, result, icon_path)


def commit_brand_result(session, domain, result, icon_path):
    """Check manual precedence atomically at the database write boundary."""
    from sqlalchemy import update, or_
    from app.models import SocialBrand
    values = {key: value for key, value in result.items() if key in {
        'display_name', 'simple_icons_slug', 'colour', 'source', 'status', 'error'}}
    values['fetched_at'] = datetime.now(timezone.utc)
    if icon_path:
        values['icon_path'] = icon_path
    session.exec(update(SocialBrand).where(SocialBrand.domain == domain,
        or_(SocialBrand.source.is_(None), SocialBrand.source != 'manual')).values(**values))
    session.commit()
