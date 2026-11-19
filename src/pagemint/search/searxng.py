"""The user's own SearXNG (PAGEMINT_SEARXNG_URL). Its JSON output must be enabled."""

from __future__ import annotations

from urllib.parse import urlencode

from pagemint.errors import FetchError, InvalidArgumentError, PageMintError
from pagemint.jobs.model import parse_date
from pagemint.safety import origin_of, private_origin_allowed
from pagemint.search.model import SearchResult, clean_results
from pagemint.search.secrets import public_url, redact

NAME = "searxng"
JSON_HINT = (
    "SearXNG refused the request (HTTP 403), probably because JSON output is off. Enable it in SearXNG's settings.yml under 'search:' with "
    "'formats: [html, json]', then restart SearXNG."
)


async def search(client, settings, query: str, limit: int) -> list[SearchResult]:
    base = (settings.searxng_url or "").rstrip("/")
    if not base:
        raise InvalidArgumentError("SearXNG is not configured: set PAGEMINT_SEARXNG_URL (e.g. http://localhost:8888).")
    shown = public_url(base)
    url = f"{base}/search?{urlencode({'q': query, 'format': 'json'})}"
    try:
        # The user configured this address, so it may be private (e.g. localhost) for this call only.
        with private_origin_allowed(origin_of(base)):
            status, data = await client.fetcher.fetch_json(url, check_robots=False, follow_redirects=False)
    except PageMintError as e:
        raise FetchError(redact(f"SearXNG at {shown} could not be reached: {e.message}", settings)) from e
    if status == 403:
        raise FetchError(JSON_HINT)
    if not 200 <= status < 300 or not isinstance(data, dict):
        raise FetchError(f"SearXNG at {shown} answered HTTP {status}.")
    rows = data.get("results")
    if not isinstance(rows, list):
        raise FetchError(f"SearXNG at {shown} gave an unexpected answer (no result list).")
    return clean_results([
        SearchResult(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("content") or ""),
                     parse_date(r.get("publishedDate")))
        for r in rows if isinstance(r, dict)
    ], limit)
