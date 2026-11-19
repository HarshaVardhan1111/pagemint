"""Tavily search API (the user's own key: PAGEMINT_TAVILY_API_KEY)."""

from __future__ import annotations

from pagemint.errors import FetchError, InvalidArgumentError
from pagemint.jobs.model import parse_date
from pagemint.search._http import call_json
from pagemint.search.model import SearchResult, clean_results
from pagemint.search.secrets import check_key

BASE = "https://api.tavily.com"
NAME = "tavily"


async def search(client, settings, query: str, limit: int) -> list[SearchResult]:
    if not settings.tavily_api_key:
        raise InvalidArgumentError("Tavily is not configured: set PAGEMINT_TAVILY_API_KEY.")
    key = check_key(settings.tavily_api_key, "PAGEMINT_TAVILY_API_KEY")
    data = await call_json(
        client, f"{BASE}/search", "Tavily", method="POST",
        headers={"Authorization": f"Bearer {key}"},
        body={"query": query, "max_results": limit},
    )
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise FetchError("Tavily gave an unexpected answer (no result list).")
    return clean_results([
        SearchResult(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("content") or ""),
                     parse_date(r.get("published_date")))
        for r in rows if isinstance(r, dict)
    ], limit)
