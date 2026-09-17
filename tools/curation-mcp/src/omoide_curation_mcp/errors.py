"""Application error codes are relayed verbatim; nothing is invented locally."""
from __future__ import annotations


class CurationError(Exception):
    """An actionable tool error. `code` is the application's code when it gave one."""

    def __init__(self, code: str, http_status: int | None = None, origin: str = 'adapter') -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.origin = origin

    def payload(self) -> dict:
        error: dict = {'code': self.code, 'origin': self.origin}
        if self.http_status is not None:
            error['http_status'] = self.http_status
        return {'error': error}
