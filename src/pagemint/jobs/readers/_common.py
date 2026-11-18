"""Shared helpers for hiring-system readers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pagemint.errors import FetchError

if TYPE_CHECKING:
    from pagemint.client import PageMint

GONE = {404, 410}
# Job feeds of large companies include every description and exceed the 5 MB page limit.
FEED_MAX_BYTES = 40_000_000


async def get_json(client: "PageMint", url: str, what: str, *, method: str = "GET", body: dict | None = None) -> Any:
    """Fetch JSON; a 404/410 raises FetchError('... not found'), other errors FetchError."""
    status, data = await client.fetcher.fetch_json(url, method=method, json_body=body, max_bytes=FEED_MAX_BYTES)
    if status in GONE:
        raise FetchError(f"{what} not found (HTTP {status}).")
    if not 200 <= status < 300 or data is None:
        raise FetchError(f"{what} could not be read (HTTP {status}).")
    return data


async def get_json_or_none(client: "PageMint", url: str, what: str) -> Any | None:
    """Like get_json, but a 404/410 returns None (used for 'is this job still open?')."""
    status, data = await client.fetcher.fetch_json(url, max_bytes=FEED_MAX_BYTES)
    if status in GONE:
        return None
    if not 200 <= status < 300 or data is None:
        raise FetchError(f"{what} could not be read (HTTP {status}).")
    return data


def remote_from_text(*texts: str | None) -> bool | None:
    for t in texts:
        if t and "remote" in t.lower():
            return True
    return None
