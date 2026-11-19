"""Brave Search API (the user's own key: PAGEMINT_BRAVE_API_KEY)."""

from __future__ import annotations

from urllib.parse import urlencode

from pagemint.errors import InvalidArgumentError
from pagemint.jobs.model import parse_date
from pagemint.search._http import call_json
from pagemint.search.model import SearchResult, clean_results

BASE = "https://api.search.brave.com"
NAME = "brave"
MAX_COUNT = 20


async def search(client, settings, query: str, limit: int) -> list[SearchResult]:
    if not settings.brave_api_key:
        raise InvalidArgumentError("Brave is not configured: set PAGEMINT_BRAVE_API_KEY.")
    qs = urlencode({"q": query, "count": min(limit, MAX_COUNT)})
    data = await call_json(
        client, f"{BASE}/res/v1/web/search?{qs}", "Brave Search",
        headers={"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"},
    )
    rows = ((data.get("web") or {}).get("results") if isinstance(data, dict) else None) or []
    return clean_results([
        SearchResult(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("description") or ""),
                     parse_date(r.get("page_age")))
        for r in rows if isinstance(r, dict)
    ], limit)
