"""Errors with messages written for the person (or agent) reading them."""

from __future__ import annotations


class PageMintError(Exception):
    type = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidURLError(PageMintError):
    type = "invalid_url"


class InvalidArgumentError(PageMintError):
    type = "invalid_argument"


class BlockedError(PageMintError):
    """The URL points at a private/internal address (SSRF protection)."""

    type = "blocked"


class RobotsError(PageMintError):
    type = "robots"


class FetchError(PageMintError):
    type = "fetch"


class TooLargeError(PageMintError):
    type = "too_large"


class ContentTypeError(PageMintError):
    type = "content_type"


class FetchTimeoutError(PageMintError):
    type = "timeout"


class BrowserUnavailableError(PageMintError):
    type = "browser_unavailable"


def error_dict(exc: BaseException) -> dict:
    if isinstance(exc, PageMintError):
        return {"error": {"type": exc.type, "message": exc.message}}
    return {"error": {"type": "internal", "message": f"Unexpected error: {type(exc).__name__}: {exc}"}}
