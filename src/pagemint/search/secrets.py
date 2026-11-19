"""Keep API keys and passwords out of every message PageMint produces."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pagemint.config import Settings
from pagemint.errors import InvalidArgumentError

MIN_SECRET_LEN = 4


def _searxng_password(settings: Settings) -> str:
    try:
        return urlsplit(settings.searxng_url or "").password or ""
    except ValueError:
        return ""


def secrets(settings: Settings) -> list[str]:
    values = (settings.tavily_api_key, settings.brave_api_key, _searxng_password(settings))
    return [k for k in values if k and len(k) >= MIN_SECRET_LEN]


def redact(text: str, settings: Settings) -> str:
    for key in secrets(settings):
        variants = {key, key.strip(), repr(key)[1:-1], key.encode("unicode_escape").decode("ascii")}
        for v in sorted(variants, key=len, reverse=True):
            if len(v) >= MIN_SECRET_LEN:
                text = text.replace(v, "***")
    return text


def check_key(value: str, variable: str) -> str:
    """A key must be visible ASCII; otherwise refuse before it is ever sent (or echoed in an error)."""
    if not value or any(not 33 <= ord(c) < 127 for c in value):
        raise InvalidArgumentError(
            f"{variable} contains spaces, line breaks or other invisible characters; set it again without them."
        )
    return value


def public_url(url: str) -> str:
    """The URL without any user:password part, safe to show."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "(invalid URL)"
    host = p.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{p.port}" if p.port else host
    return urlunsplit((p.scheme, netloc, p.path, "", ""))
