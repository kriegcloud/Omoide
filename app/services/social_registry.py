"""Pure social URL registry shared by request handling and offline migrations."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _entry(id, label, hosts, pattern, slug, colour, example):
    return dict(id=id, label=label, hosts=hosts.split(), pattern=pattern,
                simple_icons_slug=slug, colour=colour, example=example)


PLATFORMS = (
    _entry('instagram', 'Instagram', 'instagram.com', r'^/@?([^/]+)', 'instagram', '#FF0069', 'instagram.com/person'),
    _entry('tiktok', 'TikTok', 'tiktok.com', r'^/@?([^/]+)', 'tiktok', '#000000', 'tiktok.com/@person'),
    _entry('x', 'X', 'x.com twitter.com', r'^/@?([^/]+)', 'x', '#000000', 'twitter.com/person'),
    _entry('threads', 'Threads', 'threads.net threads.com', r'^/@?([^/]+)', 'threads', '#000000', 'threads.net/@person'),
    _entry('snapchat', 'Snapchat', 'snapchat.com', r'^/(?:add/)?@?([^/]+)', 'snapchat', '#FFFC00', 'snapchat.com/add/person'),
    _entry('youtube', 'YouTube', 'youtube.com youtu.be', r'^/(?:channel/|c/|user/)?@?([^/]+)', 'youtube', '#FF0000', 'youtube.com/@person'),
    _entry('facebook', 'Facebook', 'facebook.com fb.com', r'^/@?([^/]+)', 'facebook', '#0866FF', 'facebook.com/person'),
    _entry('reddit', 'Reddit', 'reddit.com', r'^/(?:user/|u/)?([^/]+)', 'reddit', '#FF4500', 'reddit.com/user/person'),
    _entry('twitch', 'Twitch', 'twitch.tv', r'^/([^/]+)', 'twitch', '#9146FF', 'twitch.tv/person'),
    _entry('linkedin', 'LinkedIn', 'linkedin.com', r'^/(?:in/|company/)?([^/]+)', None, '#0A66C2', 'linkedin.com/in/person'),
    _entry('pinterest', 'Pinterest', 'pinterest.com', r'^/([^/]+)', 'pinterest', '#BD081C', 'pinterest.com/person'),
    _entry('onlyfans', 'OnlyFans', 'onlyfans.com', r'^/([^/]+)', 'onlyfans', '#00AFF0', 'onlyfans.com/person'),
    _entry('fansly', 'Fansly', 'fansly.com', r'^/([^/]+)', None, '#2699F6', 'fansly.com/person'),
    _entry('patreon', 'Patreon', 'patreon.com', r'^/(?:c/)?([^/]+)', 'patreon', '#000000', 'patreon.com/person'),
    _entry('linktree', 'Linktree', 'linktr.ee', r'^/([^/]+)', 'linktree', '#43E55E', 'linktr.ee/person'),
    _entry('beacons', 'Beacons', 'beacons.ai', r'^/([^/]+)', None, None, 'beacons.ai/person'),
    _entry('imdb', 'IMDb', 'imdb.com', r'^/(?:name/)?([^/]+)', 'imdb', '#F5C518', 'imdb.com/name/nm123'),
    _entry('spotify', 'Spotify', 'open.spotify.com spotify.com', r'^/(?:user/|artist/)?([^/]+)', 'spotify', '#1DB954', 'open.spotify.com/artist/person'),
    _entry('soundcloud', 'SoundCloud', 'soundcloud.com', r'^/([^/]+)', 'soundcloud', '#FF5500', 'soundcloud.com/person'),
    _entry('github', 'GitHub', 'github.com', r'^/([^/]+)', 'github', '#181717', 'github.com/person'),
    _entry('bluesky', 'Bluesky', 'bsky.app', r'^/(?:profile/)?([^/]+)', 'bluesky', '#0285FF', 'bsky.app/profile/person.bsky.social'),
    _entry('mastodon', 'Mastodon', '', r'^/@([^/]+)', 'mastodon', '#6364FF', 'social.example/@person'),
    _entry('telegram', 'Telegram', 't.me telegram.me telegram.org', r'^/([^/]+)', 'telegram', '#26A5E4', 't.me/person'),
    _entry('discord', 'Discord', 'discord.com discord.gg', r'^/(?:users/|invite/)?([^/]+)', 'discord', '#5865F2', 'discord.com/users/123'),
    _entry('vsco', 'VSCO', 'vsco.co', r'^/([^/]+)', 'vsco', '#000000', 'vsco.co/person'),
    _entry('cashapp', 'Cash App', 'cash.app', r'^/\$?([^/]+)', 'cashapp', '#00C244', 'cash.app/$person'),
    _entry('venmo', 'Venmo', 'venmo.com', r'^/(?:u/)?([^/]+)', 'venmo', '#008CFF', 'venmo.com/person'),
)
TRACKING = {'igsh', 'igshid', 'si', 'fbclid', 'gclid', 'dclid', 'mc_cid', 'mc_eid'}
ALIASES = {'twitter.com': 'x.com', 'threads.com': 'threads.net', 'fb.com': 'facebook.com', 'telegram.me': 't.me'}


@lru_cache(maxsize=1)
def _suffix_rules():
    """Vendored PSL 2026-06-24, ICANN and private rules; MPL notice in data file."""
    exact, wildcard, exceptions = set(), set(), set()
    for line in Path(__file__).with_name('public_suffix_list.dat').read_text().splitlines():
        rule = line.strip()
        if not rule or rule.startswith('//'):
            continue
        target = exceptions if rule.startswith('!') else wildcard if rule.startswith('*.') else exact
        rule = rule.removeprefix('!').removeprefix('*.')
        target.add(rule.encode('idna').decode('ascii').lower())
    return exact, wildcard, exceptions


def registrable_domain(host: str) -> str:
    labels = host.lower().rstrip('.').encode('idna').decode('ascii').split('.')
    exact, wildcard, exceptions = _suffix_rules()
    suffix_size = 1
    for index in range(len(labels)):
        suffix = '.'.join(labels[index:])
        length = len(labels) - index
        if suffix in exceptions:
            return '.'.join(labels[-length:])
        if suffix in exact:
            suffix_size = max(suffix_size, length)
        if index > 0 and suffix in wildcard:
            suffix_size = max(suffix_size, length + 1)
    return '.'.join(labels[-(suffix_size + 1):])


def normalize_social_url(raw: str) -> dict:
    value = raw.strip()
    if not value or re.search(r'[\s\x00-\x1f\x7f\\]', value):
        raise ValueError('Enter a valid HTTP or HTTPS URL')
    if value.startswith('@'):
        parts = value[1:].split('@')
        if len(parts) == 2 and all(parts):
            value = f'https://{parts[1]}/@{parts[0]}'
        elif len(parts) == 1 and re.fullmatch(r'[\w.]+', parts[0]):
            value = f'https://instagram.com/{parts[0]}'
        else:
            raise ValueError('Invalid handle')
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {'http', 'https'} or parsed.username or parsed.password:
        raise ValueError('Only HTTP and HTTPS URLs without credentials are allowed')
    host = (parsed.hostname or '').lower().rstrip('.')
    while host.startswith(('www.', 'm.')):
        host = host.split('.', 1)[1]
    if not host or '.' not in host or ':' in host:
        raise ValueError('URL must have a domain name')
    try:
        host = host.encode('idna').decode('ascii')
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid URL host or port') from exc
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) for label in host.split('.')):
        raise ValueError('Invalid URL domain')
    host = ALIASES.get(host, host)
    platform = next((p for p in PLATFORMS if host in p['hosts']), None)
    if platform is None and re.match(r'^/@[^/]+/?$', parsed.path):
        platform = next(p for p in PLATFORMS if p['id'] == 'mastodon')
    path = parsed.path.rstrip('/')
    query = urlencode(sorted((key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.lower().startswith('utm_') and key.lower() not in TRACKING))
    scheme = 'https' if platform else parsed.scheme.lower()
    default_port = 443 if scheme == 'https' else 80
    netloc = host + (f':{port}' if port and port != default_port else '')
    url = urlunsplit((scheme, netloc, path, query, ''))
    match = re.match(platform['pattern'], path) if platform else None
    handle = match.group(1) if match else host
    # Registry host is the brand identity; arbitrary websites use their own host.
    domain = registrable_domain(host)
    return dict(platform=platform['id'] if platform else 'other', handle=handle,
                url=url, normalized_url=url, brand_domain=domain)


def brand_metadata(domain: str, platform_id: str | None = None) -> dict:
    platform = next((p for p in PLATFORMS if domain in p['hosts']), None)
    if platform is None and platform_id == 'mastodon':
        platform = next(p for p in PLATFORMS if p['id'] == 'mastodon')
    return dict(display_name=platform['label'] if platform else domain,
                simple_icons_slug=platform['simple_icons_slug'] if platform else None,
                colour=platform['colour'] if platform else None)
