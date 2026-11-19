"""The search tool: pick the configured provider, search, optionally read the top pages."""

from __future__ import annotations

import asyncio
import logging

from pagemint.errors import InvalidArgumentError, PageMintError, error_dict
from pagemint.search import brave, searxng, tavily
from pagemint.search.secrets import redact

log = logging.getLogger("pagemint.search")

PROVIDERS = {"searxng": searxng, "tavily": tavily, "brave": brave}
MAX_LIMIT = 20
MAX_FETCH = 5
PAGES_BUDGET = 60.0  # seconds for all fetch_pages together

NOT_CONFIGURED = (
    "Web search is not set up. Choose one: "
    "(1) free, no key: run your own SearXNG, e.g. "
    "'docker run -d --name searxng -p 8888:8080 -v ./searxng:/etc/searxng searxng/searxng', "
    "add 'search: formats: [html, json]' to ./searxng/settings.yml, restart it, and set "
    "PAGEMINT_SEARXNG_URL=http://localhost:8888; or "
    "(2) your own API key: set PAGEMINT_TAVILY_API_KEY or PAGEMINT_BRAVE_API_KEY. "
    "PageMint never uses a paid service unless you set its key."
)


def configured(settings) -> list[str]:
    out = []
    if settings.searxng_url:
        out.append("searxng")
    if settings.tavily_api_key:
        out.append("tavily")
    if settings.brave_api_key:
        out.append("brave")
    return out


def _clamp(n, lo, hi, default):
    try:
        return max(lo, min(hi, int(n)))
    except (TypeError, ValueError):
        return default


async def _fetch_pages(client, urls: list[str]) -> list[dict]:
    tasks = {asyncio.ensure_future(client.scrape(u)): u for u in urls}
    done, pending = await asyncio.wait(tasks, timeout=PAGES_BUDGET)
    for t in pending:
        t.cancel()
    pages = []
    for t, url in tasks.items():
        if t in pending:
            pages.append({"url": url, "error": {"type": "timeout", "message": "Page took too long; time limit reached."}})
            continue
        r = t.result()
        if "error" in r:
            pages.append({"url": url, "error": r["error"]})
        else:
            pages.append({"url": r["url"], "title": r.get("title"), "markdown": r["content"][:20_000]})
    return pages


async def run_search(client, query: str, limit: int = 10, fetch_pages: int = 0, provider: str = "auto") -> dict:
    settings = client.settings
    try:
        if not (query or "").strip():
            raise InvalidArgumentError("Give a search query.")
        limit = _clamp(limit, 1, MAX_LIMIT, 10)
        fetch_pages = _clamp(fetch_pages, 0, MAX_FETCH, 0)
        provider = (provider or "auto").strip().lower()
        if provider != "auto" and provider not in PROVIDERS:
            raise InvalidArgumentError(f"Unknown provider '{provider}'. Use auto, {', '.join(PROVIDERS)}.")

        available = configured(settings)
        if not available:
            return {"query": query, "error": {"type": "not_configured", "message": NOT_CONFIGURED}}
        env_choice = (settings.search_provider or "").strip().lower()
        if env_choice == "auto":
            env_choice = ""
        if env_choice and env_choice not in PROVIDERS:
            raise InvalidArgumentError(
                f"PAGEMINT_SEARCH_PROVIDER='{env_choice}' is not a provider. Use auto, {', '.join(PROVIDERS)}."
            )
        pinned = provider != "auto" or bool(env_choice)
        chosen = provider if provider != "auto" else (env_choice or available[0])
        if chosen not in available:
            raise InvalidArgumentError(
                f"'{chosen}' is not configured. Configured: {', '.join(available)}."
            )
        candidates = [chosen]
        if settings.search_fallback and not pinned:
            candidates += [p for p in available if p != chosen]

        warnings: list[str] = []
        results = None
        used = None
        last_error: PageMintError | None = None
        for i, name in enumerate(candidates):
            try:
                results = await PROVIDERS[name].search(client, settings, query.strip(), limit)
                used = name
                break
            except PageMintError as e:
                last_error = e
                if i + 1 < len(candidates):
                    warnings.append(f"{name} failed ({e.message}); used {candidates[i + 1]} instead.")
        if results is None:
            others = [p for p in available if p != chosen]
            msg = last_error.message if last_error else "search failed"
            if others and not settings.search_fallback:
                msg += (f" Other configured providers: {', '.join(others)}. Pick one with provider=..., or set "
                        "PAGEMINT_SEARCH_FALLBACK=1 to try them automatically.")
            return {"query": query, "error": {"type": last_error.type if last_error else "fetch",
                                              "message": redact(msg, settings)}}

        out = {"query": query, "provider": used, "results": [r.to_dict() for r in results],
               "warnings": [redact(w, settings) for w in warnings]}
        if fetch_pages:
            out["pages"] = await _fetch_pages(client, [r.url for r in results[:fetch_pages]])
        return out
    except Exception as e:
        if not isinstance(e, PageMintError):
            log.error("search failed: %s", redact(f"{type(e).__name__}: {e}", settings))
        err = error_dict(e)
        err["error"]["message"] = redact(err["error"]["message"], settings)
        return {"query": query, **err}
