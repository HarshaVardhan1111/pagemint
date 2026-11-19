"""Daily per-user limits, server-wide caps and concurrency limits for hosted PageMint."""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from pagemint.errors import PageMintError
from pagemint.hosted.store import HostedStore

DEFAULT_LIMITS = {"pages": 200, "job_searches": 100, "web_searches": 50, "renders": 30}
GLOBAL_LIMITS = {"pages": 5000, "web_searches": 500, "job_searches": 2000}
NAMES = {"pages": "pages", "job_searches": "job searches", "web_searches": "web searches",
         "renders": "JavaScript page renders"}
PER_USER_CONCURRENT = 2
GLOBAL_CONCURRENT = 8
HOSTED_CRAWL_CAP = 50
EVERYONE = "*"

# Who the current tool call is for (set by the server around each hosted tool call).
CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar("pagemint_current_user", default="local")


class LimitReached(PageMintError):
    type = "limit_reached"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Limits:
    def __init__(self, store: HostedStore, clock=_utc_now, per_user: dict | None = None):
        self.store = store
        self.clock = clock
        self.per_user = dict(per_user or DEFAULT_LIMITS)
        self._active: dict[str, int] = {}
        self._total = 0

    def day(self) -> str:
        return self.clock().date().isoformat()

    def reset_text(self) -> str:
        now = self.clock()
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        mins = int((midnight - now).total_seconds() // 60)
        return f"It resets at 00:00 UTC (in {mins // 60}h {mins % 60}m)."

    def remaining(self, user: str, kind: str) -> int:
        day = self.day()
        left = self.per_user[kind] - self.store.get_usage(user, day, kind)
        if kind in GLOBAL_LIMITS:
            left = min(left, GLOBAL_LIMITS[kind] - self.store.get_usage(EVERYONE, day, kind))
        return max(0, left)

    def check(self, user: str, kind: str, n: int = 1) -> None:
        day = self.day()
        name = NAMES[kind]
        if self.per_user[kind] - self.store.get_usage(user, day, kind) < n:
            raise LimitReached(f"You have reached today's limit of {self.per_user[kind]} {name}. {self.reset_text()}")
        if kind in GLOBAL_LIMITS and GLOBAL_LIMITS[kind] - self.store.get_usage(EVERYONE, day, kind) < n:
            raise LimitReached(
                f"PageMint has reached today's limit of {GLOBAL_LIMITS[kind]} {name} for everyone. {self.reset_text()}"
            )

    def charge(self, user: str, kind: str, n: int = 1) -> None:
        if n <= 0:
            return
        day = self.day()
        self.store.add_usage(user, day, kind, n)
        if kind in GLOBAL_LIMITS:
            self.store.add_usage(EVERYONE, day, kind, n)

    @asynccontextmanager
    async def guard(self, user: str):
        if self._active.get(user, 0) >= PER_USER_CONCURRENT:
            raise LimitReached(
                f"Too many PageMint requests at once (at most {PER_USER_CONCURRENT}). Wait for one to finish."
            )
        if self._total >= GLOBAL_CONCURRENT:
            raise LimitReached("PageMint is busy right now. Please try again in a moment.")
        self._active[user] = self._active.get(user, 0) + 1
        self._total += 1
        try:
            yield
        finally:
            self._active[user] -= 1
            self._total -= 1


class RenderGate:
    """One browser render at a time for the whole server, each charged to the caller's daily budget."""

    def __init__(self, limits: Limits):
        self.limits = limits
        self._sem = asyncio.Semaphore(1)

    @asynccontextmanager
    async def slot(self):
        user = CURRENT_USER.get()
        self.limits.check(user, "renders")
        async with self._sem:
            self.limits.charge(user, "renders", 1)
            yield
