"""Calls to search providers: no robots.txt (keyed API use), no redirects, keys redacted."""

from __future__ import annotations

from pagemint.errors import FetchError, PageMintError
from pagemint.search.secrets import redact

_HINTS = {
    401: "the API key was not accepted; check it",
    403: "access was refused; check the API key and plan",
    429: "rate limit or monthly quota reached",
}


async def call_json(client, url: str, what: str, *, method: str = "GET", headers: dict | None = None,
                    body: dict | None = None):
    settings = client.settings
    try:
        status, data = await client.fetcher.fetch_json(
            url, method=method, json_body=body, headers=headers, follow_redirects=False, check_robots=False
        )
    except PageMintError as e:
        e.message = redact(f"{what}: {e.message}", settings)
        e.args = (e.message,)
        raise
    if not 200 <= status < 300 or data is None:
        hint = _HINTS.get(status) or ("the service had a problem" if status >= 500 else "unexpected answer")
        raise FetchError(f"{what} answered HTTP {status}: {hint}.")
    return data
