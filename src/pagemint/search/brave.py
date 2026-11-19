"""Brave Search API (the user's own key: PAGEMINT_BRAVE_API_KEY)."""

from __future__ import annotations

from urllib.parse import urlencode

from pagemint.errors import FetchError, InvalidArgumentError
from pagemint.jobs.model import parse_date
from pagemint.search._http import call_json
from pagemint.search.model import SearchResult, clean_results
from pagemint.search.secrets import check_key

BASE = "https://api.search.brave.com"
NAME = "brave"
MAX_COUNT = 20


async def search(client, settings, query: str, limit: int) -> list[SearchResult]:
    if not settings.brave_api_key:
        raise InvalidArgumentError("Brave is not configured: set PAGEMINT_BRAVE_API_KEY.")
    key = check_key(settings.brave_api_key, "PAGEMINT_BRAVE_API_KEY")
    qs = urlencode({"q": query, "count": min(limit, MAX_COUNT)})
    data = await call_json(
        client, f"{BASE}/res/v1/web/search?{qs}", "Brave Search",
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    web = data.get("web") if isinstance(data, dict) else None
    rows = web.get("results") if isinstance(web, dict) else None
    if rows is None and isinstance(data, dict) and "web" not in data:
        rows = []  # Brave omits "web" when nothing matched
    if not isinstance(rows, list):
        raise FetchError("Brave Search gave an unexpected answer (no result list).")
    return clean_results([
        SearchResult(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("description") or ""),
                     parse_date(r.get("page_age")))  # "age" is relative text ("2 days ago"), not a date
        for r in rows if isinstance(r, dict)
    ], limit)
