"""HTTP fetching with safety checks on every hop.

Each request (and each redirect hop) is: URL check -> DNS + SSRF check ->
robots.txt check -> per-host rate limit -> streamed GET with a size cap.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin
from urllib.request import getproxies, proxy_bypass

import httpcore
import httpx

from pagemint.config import Settings
from pagemint.errors import (
    ContentTypeError,
    FetchError,
    FetchTimeoutError,
    InvalidURLError,
    RobotsError,
    TooLargeError,
)
from pagemint.links import normalize_url
from pagemint.ratelimit import RateLimiter
from pagemint.robots import RobotsCache
from pagemint.safety import Resolver, resolve_and_check, validate_url

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_REDIRECT_CODES = {301, 302, 303, 307, 308}


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


# Browsers decode these labels as windows-1252 (WHATWG Encoding Standard).
_CP1252_ALIASES = {"iso-8859-1", "iso8859-1", "latin1", "latin-1", "l1", "us-ascii", "ascii", "windows-1252", "cp1252"}


def _label(enc: str) -> str:
    enc = enc.strip().lower()
    return "cp1252" if enc in _CP1252_ALIASES else enc


def decode_body(content: bytes, content_type_header: str | None) -> str:
    """Decode like a browser: BOM, header charset, <meta charset>, UTF-8, then windows-1252."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", errors="replace")
    candidates: list[str] = []
    m = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type_header or "", re.I)
    if m:
        candidates.append(_label(m.group(1)))
    m2 = _META_CHARSET_RE.search(content[:4096])
    if m2:
        candidates.append(_label(m2.group(1).decode("ascii", "ignore")))
    candidates.append("utf-8")
    for enc in candidates:
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("cp1252", errors="replace")


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves and SSRF-checks the host at the moment of connecting, then
    connects to the checked address. This closes the gap between the check
    and the connection (DNS rebinding). Connections stay pooled by host name,
    so TLS SNI and certificate checks use the real name."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend, fetcher: "Fetcher"):
        self._inner = inner
        self._fetcher = fetcher

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        f = self._fetcher
        addrs = await resolve_and_check(host, port, f.settings.allow_private_networks, f.resolver)
        last: Exception | None = None
        for ip in addrs:
            try:
                return await self._inner.connect_tcp(ip, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as e:
                last = e
        assert last is not None
        raise last

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover
        raise httpcore.ConnectError("Unix sockets are not supported.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _mb(n: int) -> str:
    return f"{n / 1_000_000:g} MB"


class Fetcher:
    def __init__(self, settings: Settings, resolver: Resolver | None = None):
        self.settings = settings
        self.resolver = resolver
        self.rate = RateLimiter(settings.rate_limit)
        self._proxies = {k.lower(): v for k, v in getproxies().items()}
        self.robots = RobotsCache(settings.user_agent, self._fetch_robots, ttl=settings.robots_cache_ttl)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            },
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            follow_redirects=False,
        )
        # Direct connections go through the guarded backend. Requests sent via a
        # configured proxy are checked before sending (the proxy connects).
        pool = self._client._transport._pool  # httpx 0.2x / httpcore 1.x internals
        pool._network_backend = GuardedBackend(pool._network_backend, self)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- public --------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        page: bool = True,
        check_robots: bool = True,
        method: str = "GET",
        json_body: dict | None = None,
        max_bytes: int | None = None,
        headers: dict | None = None,
        follow_redirects: bool = True,
    ) -> FetchResult:
        """Fetch ``url`` safely, following redirects (each hop re-checked).

        With ``page=True`` only HTML/plain-text responses are accepted and the
        body is decoded into ``text``. ``method="POST"`` sends ``json_body`` as
        JSON; redirects are not followed for POST.
        """
        method = method.upper()
        if method not in ("GET", "POST"):
            raise ValueError(f"unsupported method {method}")
        # The page timeout starts once the rate limiter lets the first request go,
        # so waiting in line behind other requests to the same site does not count.
        return await self._fetch(url, page, check_robots, method, json_body, max_bytes, headers, follow_redirects)

    async def fetch_json(
        self,
        url: str,
        *,
        method: str = "GET",
        json_body: dict | None = None,
        max_bytes: int | None = None,
        headers: dict | None = None,
        follow_redirects: bool = True,
        check_robots: bool = True,
    ) -> tuple[int, object]:
        """Fetch a JSON API. Returns ``(status_code, data)``; data is None for non-2xx answers.

        ``max_bytes`` overrides the page size limit for this call (job feeds can be large).
        """
        res = await self.fetch(
            url, page=False, method=method, json_body=json_body, max_bytes=max_bytes, headers=headers,
            follow_redirects=follow_redirects, check_robots=check_robots,
        )
        if not 200 <= res.status_code < 300:
            return res.status_code, None
        try:
            return res.status_code, json.loads(res.content.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise FetchError(f"{url} did not return JSON ({res.content_type or 'unknown type'}).") from e

    async def check_host(self, url: str) -> None:
        """Validate the URL and run the SSRF check on its host without fetching."""
        scheme, host, port = validate_url(url)
        await resolve_and_check(
            host, port, self.settings.allow_private_networks, self.resolver, via_proxy=self._via_proxy(scheme, host)
        )

    async def check_allowed(self, url: str) -> None:
        """Raise RobotsError if robots.txt disallows ``url`` (no-op when robots is off)."""
        if not self.settings.respect_robots:
            return
        entry = await self.robots.entry(url)
        if entry.unreachable:
            raise FetchError(f"Could not reach {RobotsCache.origin(url)}: {entry.error}.")
        ok, reason = await self.robots.allowed(url)
        if not ok:
            raise RobotsError(reason)

    # ---- internals -----------------------------------------------------

    async def _fetch_robots(self, url: str) -> FetchResult:
        return await self.fetch(url, page=False, check_robots=False)

    def _via_proxy(self, scheme: str, host: str) -> bool:
        if not (self._proxies.get(scheme) or self._proxies.get("all")):
            return False
        try:
            return not proxy_bypass(host)
        except Exception:
            return True

    async def _fetch(
        self, url: str, page: bool, check_robots: bool, method: str = "GET", json_body=None,
        max_bytes: int | None = None, headers: dict | None = None, follow_redirects: bool = True,
    ) -> FetchResult:
        s = self.settings
        current = url
        redirects: list[str] = []
        deadline: float | None = None
        for _ in range(s.max_redirects + 1):
            scheme, host, port = validate_url(current)
            await resolve_and_check(
                host, port, s.allow_private_networks, self.resolver, via_proxy=self._via_proxy(scheme, host)
            )
            if check_robots and s.respect_robots:
                await self.check_allowed(current)
                delay = await self.robots.crawl_delay(current)
                if delay:
                    self.rate.set_interval(host, delay)
            await self.rate.wait(host)
            if deadline is None:
                deadline = time.monotonic() + s.page_timeout
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise asyncio.TimeoutError
                res = await asyncio.wait_for(
                    self._get(current, page, method, json_body, max_bytes or s.max_bytes, headers), timeout=remaining
                )
            except asyncio.TimeoutError as e:
                raise FetchTimeoutError(f"Timed out fetching {url} after {s.page_timeout:g} seconds in total.") from e
            if res is None:
                raise FetchError(f"Empty response from {current}.")
            if isinstance(res, str):  # redirect target
                if not follow_redirects:
                    raise FetchError(f"{current} answered with a redirect; not followed for this request.")
                if method != "GET":
                    raise FetchError(f"{current} answered a {method} request with a redirect; not followed.")
                nxt = normalize_url(res, current)
                if nxt is None:
                    raise InvalidURLError(f"{current} redirected to '{res}', which is not an http(s) URL.")
                redirects.append(current)
                current = nxt
                continue
            res.redirects = redirects
            return res
        raise FetchError(f"Too many redirects (more than {s.max_redirects}) starting at {url}.")

    async def _get(
        self, url: str, page: bool, method: str = "GET", json_body=None, limit: int | None = None,
        headers: dict | None = None,
    ) -> FetchResult | str | None:
        s = self.settings
        limit = limit or s.max_bytes
        kwargs: dict = {"json": json_body if json_body is not None else {}} if method == "POST" else {}
        if headers:
            kwargs["headers"] = headers
        try:
            async with self._client.stream(method, url, **kwargs) as resp:
                if resp.status_code in _REDIRECT_CODES and resp.headers.get("location"):
                    return urljoin(url, resp.headers["location"].strip())
                ctype_header = resp.headers.get("content-type")
                mtype = media_type(ctype_header)
                if page and mtype and mtype not in _TEXT_TYPES:
                    raise ContentTypeError(
                        f"Unsupported content type '{mtype}' at {url}. PageMint reads HTML and plain text pages."
                    )
                too_large = TooLargeError(f"{url} is larger than the {limit} byte limit ({_mb(limit)}).")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise too_large
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise too_large
                    chunks.append(chunk)
                content = b"".join(chunks)
                result = FetchResult(
                    url=url,
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                    content=content,
                    content_type=mtype or "text/html",
                )
                if page:
                    result.text = decode_body(content, ctype_header)
                return result
        except httpx.TimeoutException as e:
            raise FetchTimeoutError(f"Timed out fetching {url} ({type(e).__name__}).") from e
        except httpx.HTTPError as e:
            raise FetchError(f"Could not fetch {url}: {type(e).__name__}: {e}") from e
