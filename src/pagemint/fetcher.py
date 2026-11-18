"""HTTP fetching with httpx."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from pagemint.config import Settings
from pagemint.errors import ContentTypeError, FetchError, FetchTimeoutError

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    content_type: str  # lowercased media type without parameters
    text: str = ""
    redirects: list[str] = field(default_factory=list)

    @property
    def is_html(self) -> bool:
        return self.content_type in ("text/html", "application/xhtml+xml")


def media_type(content_type_header: str | None) -> str:
    return (content_type_header or "").split(";", 1)[0].strip().lower()


def decode_body(content: bytes, content_type_header: str | None) -> str:
    """Decode using the header charset, then <meta charset>, then UTF-8."""
    candidates: list[str] = []
    m = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type_header or "", re.I)
    if m:
        candidates.append(m.group(1))
    m2 = _META_CHARSET_RE.search(content[:4096])
    if m2:
        candidates.append(m2.group(1).decode("ascii", "ignore"))
    if content.startswith(b"\xef\xbb\xbf"):
        candidates.insert(0, "utf-8-sig")
    candidates.append("utf-8")
    for enc in candidates:
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"},
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            follow_redirects=True,
            max_redirects=settings.max_redirects,
        )

    async def fetch(self, url: str, *, page: bool = True) -> FetchResult:
        """GET ``url``. With ``page=True`` only HTML/plain-text responses are accepted."""
        try:
            resp = await self._client.get(url)
        except httpx.TimeoutException as e:
            raise FetchTimeoutError(f"Timed out fetching {url} ({type(e).__name__}).") from e
        except httpx.TooManyRedirects as e:
            raise FetchError(f"Too many redirects (more than {self.settings.max_redirects}) for {url}.") from e
        except httpx.HTTPError as e:
            raise FetchError(f"Could not fetch {url}: {type(e).__name__}: {e}") from e
        ctype_header = resp.headers.get("content-type")
        mtype = media_type(ctype_header)
        if page and mtype and mtype not in _TEXT_TYPES:
            raise ContentTypeError(f"Unsupported content type '{mtype}' at {url}. PageMint reads HTML and plain text pages.")
        result = FetchResult(
            url=str(resp.url),
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
            content_type=mtype or "text/html",
        )
        if page:
            result.text = decode_body(resp.content, ctype_header)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
