"""Per-host rate limiting."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Spaces requests to the same host at least ``interval`` seconds apart.

    Different hosts never wait for each other. A host can get a longer interval
    (for example from robots.txt ``Crawl-delay``) with :meth:`set_interval`.
    """

    def __init__(self, interval: float):
        self.interval = max(0.0, float(interval))
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._custom: dict[str, float] = {}

    def set_interval(self, host: str, seconds: float) -> None:
        """Use ``seconds`` for ``host`` if it is longer than the default."""
        if seconds > self.interval:
            self._custom[host] = seconds

    def interval_for(self, host: str) -> float:
        return self._custom.get(host, self.interval)

    async def wait(self, host: str) -> None:
        interval = self.interval_for(host)
        if interval <= 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            ready = self._next.get(host, now)
            if ready > now:
                await asyncio.sleep(ready - now)
                now = time.monotonic()
            self._next[host] = now + interval
