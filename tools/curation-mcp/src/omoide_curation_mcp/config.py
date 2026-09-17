"""Endpoint and credential configuration. Credentials never come from tool arguments."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import CurationError

DEFAULT_URL = 'http://127.0.0.1:8123'
LOOPBACK_HOSTS = {'127.0.0.1', '::1', 'localhost'}
MAX_CREDENTIAL_BYTES = 4096
# The application accepts 16..256 character bearers (curation_policy.authorize).
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 256


def bounded_int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise CurationError('invalid_configuration') from None
    if not low <= value <= high:
        raise CurationError('invalid_configuration')
    return value


def bounded_float(raw: str | None, default: float, low: float, high: float) -> float:
    try:
        value = float(raw) if raw else default
    except ValueError:
        raise CurationError('invalid_configuration') from None
    if not low <= value <= high:
        raise CurationError('invalid_configuration')
    return value


def normalize_url(raw: str) -> str:
    """Loopback by default; plaintext HTTP is refused off-loopback so a bearer is
    never written to a cleartext remote socket."""
    split = urlsplit(raw)
    if split.scheme not in {'http', 'https'} or not split.hostname:
        raise CurationError('invalid_omoide_url')
    if split.query or split.fragment or split.username or split.password:
        raise CurationError('invalid_omoide_url')
    if split.path not in {'', '/'}:
        raise CurationError('invalid_omoide_url')
    if split.scheme == 'http' and split.hostname not in LOOPBACK_HOSTS:
        raise CurationError('insecure_omoide_url')
    return f'{split.scheme}://{split.netloc}'


@dataclass(frozen=True)
class Config:
    base_url: str
    credential_path: str | None
    timeout_seconds: float
    max_result_bytes: int
    max_preview_bytes: int

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> 'Config':
        values = os.environ if environ is None else environ
        return cls(
            base_url=normalize_url(values.get('OMOIDE_URL') or DEFAULT_URL),
            credential_path=values.get('OMOIDE_CURATION_CREDENTIAL_FILE') or None,
            timeout_seconds=bounded_float(values.get('OMOIDE_MCP_TIMEOUT_SECONDS'), 120.0, 1.0, 900.0),
            max_result_bytes=bounded_int(values.get('OMOIDE_MCP_MAX_RESULT_BYTES'), 262144, 4096, 4194304),
            max_preview_bytes=bounded_int(values.get('OMOIDE_MCP_MAX_PREVIEW_BYTES'), 2097152, 1024, 8388608),
        )


def load_credential(path: str | None) -> str:
    """Read the bearer from an owner-only regular file. The value is never logged,
    never echoed into a tool result and never accepted as a tool argument."""
    if not path:
        raise CurationError('credential_file_unconfigured')
    if not os.path.isabs(path):
        raise CurationError('credential_file_not_absolute')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise CurationError('credential_file_unreadable') from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise CurationError('credential_file_not_regular')
        if info.st_uid != os.geteuid():
            raise CurationError('credential_file_foreign_owner')
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            # 0600 is required: a group- or world-readable bearer is refused.
            raise CurationError('credential_file_permissions')
        if info.st_size > MAX_CREDENTIAL_BYTES:
            raise CurationError('credential_file_too_large')
        raw = os.read(descriptor, MAX_CREDENTIAL_BYTES)
    finally:
        os.close(descriptor)
    try:
        token = raw.decode('utf-8').strip()
    except UnicodeDecodeError:
        raise CurationError('credential_file_invalid') from None
    if not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH or not token.isprintable():
        raise CurationError('credential_file_invalid')
    return token
