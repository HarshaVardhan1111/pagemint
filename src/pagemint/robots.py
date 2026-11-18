"""robots.txt parsing and caching (RFC 9309), written for PageMint.

Supports user-agent groups, Allow/Disallow with ``*`` and ``$`` wildcards,
longest-match precedence (Allow wins ties), Crawl-delay and Sitemap lines.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable
from urllib.parse import quote, unquote, urlsplit

from pagemint.errors import BlockedError, FetchError, PageMintError

if TYPE_CHECKING:
    from pagemint.fetcher import FetchResult

AGENT_TOKEN = "pagemint"
ERROR_TTL = 60.0
_SAFE = "/%:@!$&'()*+,;=-._~?#[]"


def _norm_path(path: str) -> str:
    # Percent-encode consistently so "/café" and "/caf%C3%A9" compare equal.
    return quote(unquote(path), safe=_SAFE)


@dataclass
class _Rule:
    allow: bool
    pattern: str  # normalized
    raw: str
    regex: re.Pattern

    @classmethod
    def make(cls, allow: bool, value: str) -> "_Rule":
        pattern = _norm_path(value)
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        rx = "^" + rx + ("$" if anchored else "")
        return cls(allow, pattern, f"{'Allow' if allow else 'Disallow'}: {value}", re.compile(rx))


@dataclass
class RobotsRules:
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None

    def check(self, path: str) -> tuple[bool, str | None]:
        """Return ``(allowed, matching_rule)`` for a path (with query string)."""
        path = _norm_path(path or "/")
        if path == "/robots.txt":
            return True, None
        best: _Rule | None = None
        for rule in self.rules:
            if rule.regex.match(path):
                if (
                    best is None
                    or len(rule.pattern) > len(best.pattern)
                    or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
                ):
                    best = rule
        if best is None:
            return True, None
        return best.allow, best.raw


ALLOW_ALL = RobotsRules()


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class RobotsFile:
    groups: list[_Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)

    def rules_for(self, user_agent: str) -> RobotsRules:
        token = user_agent.split("/", 1)[0].strip().lower() or AGENT_TOKEN
        mine = [g for g in self.groups if any(a == token for a in g.agents)]
        if not mine:
            mine = [g for g in self.groups if "*" in g.agents]
        if not mine:
            return ALLOW_ALL
        rules = [r for g in mine for r in g.rules]
        delays = [g.crawl_delay for g in mine if g.crawl_delay is not None]
        return RobotsRules(rules=rules, crawl_delay=max(delays) if delays else None)


def parse_robots(text: str) -> RobotsFile:
    out = RobotsFile()
    current: _Group | None = None
    last_was_agent = False
    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = _Group()
                out.groups.append(current)
            current.agents.append(value.split("/", 1)[0].strip().lower())
            last_was_agent = True
            continue
        if key == "sitemap":
            if value:
                out.sitemaps.append(value)
            continue  # sitemap lines do not end a user-agent run
        last_was_agent = False
        if current is None:
            continue
        if key in ("allow", "disallow"):
            if value:  # an empty Disallow means "allow everything"
                current.rules.append(_Rule.make(key == "allow", value))
        elif key == "crawl-delay":
            try:
                current.crawl_delay = max(0.0, float(value))
            except ValueError:
                pass
    return out


FetchRaw = Callable[[str], Awaitable["FetchResult"]]


@dataclass
class _Entry:
    rules: RobotsRules
    sitemaps: list[str]
    error: str | None  # set when robots.txt could not be read (host is then disallowed)
    expires: float
    unreachable: bool = False  # the host could not be reached at all


class RobotsCache:
    """Fetches robots.txt once per origin and answers allow/deny questions."""

    def __init__(self, user_agent: str, fetch_raw: FetchRaw, ttl: float = 3600.0, max_entries: int = 1000):
        self.user_agent = user_agent
        self._fetch_raw = fetch_raw
        self.ttl = ttl
        self.max_entries = max_entries
        self._cache: dict[str, _Entry] = {}
        self._pending: dict[str, asyncio.Future[_Entry]] = {}

    @staticmethod
    def origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    async def _load(self, origin: str) -> _Entry:
        robots_url = origin + "/robots.txt"
        now = time.monotonic()
        expires = now + self.ttl
        retry = now + min(self.ttl, ERROR_TTL)  # failures are retried sooner
        try:
            res = await self._fetch_raw(robots_url)
        except BlockedError:
            raise  # a blocked address is reported as such, not as a robots problem
        except PageMintError as e:
            msg = f"robots.txt at {robots_url} could not be fetched ({e.message})"
            return _Entry(ALLOW_ALL, [], msg, retry, unreachable=isinstance(e, FetchError))
        if 200 <= res.status_code < 300:
            f = parse_robots(res.content.decode("utf-8-sig", errors="replace"))
            return _Entry(f.rules_for(self.user_agent), f.sitemaps, None, expires)
        if 400 <= res.status_code < 500:
            return _Entry(ALLOW_ALL, [], None, expires)  # no robots.txt: everything allowed
        return _Entry(ALLOW_ALL, [], f"robots.txt at {robots_url} returned HTTP {res.status_code}", retry)

    async def entry(self, url: str) -> _Entry:
        origin = self.origin(url)
        e = self._cache.get(origin)
        if e is not None and e.expires > time.monotonic():
            return e
        task = self._pending.get(origin)
        if task is None:
            # The cache owns the load; callers only wait on it. A caller being
            # cancelled (e.g. its page timed out) must not cancel the load for
            # other requests waiting on the same site.
            task = asyncio.ensure_future(self._load(origin))
            self._pending[origin] = task
            task.add_done_callback(lambda t, o=origin: self._finish(o, t))
        return await asyncio.shield(task)

    def _finish(self, origin: str, task: "asyncio.Future[_Entry]") -> None:
        self._pending.pop(origin, None)
        if task.cancelled() or task.exception() is not None:
            return
        if len(self._cache) >= self.max_entries:
            self._cache.pop(next(iter(self._cache)))
        self._cache[origin] = task.result()

    async def allowed(self, url: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)``. The reason explains a refusal."""
        e = await self.entry(url)
        if e.error:
            return False, f"Skipped {url}: {e.error}, so PageMint treats the whole site as off-limits for now."
        p = urlsplit(url)
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        ok, rule = e.rules.check(path)
        if ok:
            return True, ""
        return False, f"Blocked by robots.txt (rule: {rule}) for {url}."

    async def crawl_delay(self, url: str) -> float | None:
        return (await self.entry(url)).rules.crawl_delay

    async def sitemaps(self, url: str) -> list[str]:
        return list((await self.entry(url)).sitemaps)
