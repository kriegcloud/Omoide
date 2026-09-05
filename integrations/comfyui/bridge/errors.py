"""Stable failure types for the Omoide ComfyUI bridge."""

from __future__ import annotations


class BridgeError(RuntimeError):
    """A classified failure safe to return across the Unix socket."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_response(self, protocol: str) -> dict[str, object]:
        return {
            "ok": False,
            "protocol": protocol,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }
