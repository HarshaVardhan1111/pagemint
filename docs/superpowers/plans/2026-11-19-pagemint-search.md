# PageMint Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `search` MCP tool and CLI command that query the user's own SearXNG or their Tavily/Brave key, optionally reading the top result pages as Markdown.

**Architecture:** New package `src/pagemint/search/` with one module per provider and a `run_search` service. The core fetcher gains request headers, a no-redirect mode, and a context-scoped "private origin allowed" switch used only for the configured SearXNG origin. Keys live only in `Settings` (from environment variables) and are redacted from every message.

**Tech Stack:** existing deps only.

**Spec:** `docs/superpowers/specs/2026-11-19-pagemint-search-design.md`

## Global Constraints

- Keys only from env vars `PAGEMINT_TAVILY_API_KEY`, `PAGEMINT_BRAVE_API_KEY`; never CLI flags, never tool arguments; never in output, warnings, errors or logs.
- Other settings: `PAGEMINT_SEARXNG_URL`, `PAGEMINT_SEARCH_PROVIDER` (`searxng|tavily|brave`), `PAGEMINT_SEARCH_FALLBACK` (off by default).
- `limit` 1–20 (default 10); `fetch_pages` 0–5; page Markdown capped at 20,000 chars.
- Selection: argument (not `auto`) > `PAGEMINT_SEARCH_PROVIDER` > SearXNG URL set > Tavily key > Brave key. Fallback only when enabled AND not pinned; triggers on any provider failure.
- Keyed calls never follow redirects; Tavily/Brave base URLs fixed https constants (overridable only in tests); provider API calls and SearXNG skip robots.txt; `fetch_pages` uses the normal scrape (robots, SSRF, limits).
- Only the exact SearXNG origin may be private, only during the SearXNG call.
- Tools never raise. Offline tests only.

## Review Focus

1. SearXNG without JSON enabled answers 403 HTML → error explains how to enable `formats: [html, json]`. Test in Task 3.
2. Provider results with missing title/url, `javascript:`/relative URLs, or duplicates → dropped, no crash. Test in Task 3.
3. A key appearing in any exception or HTTP error text → redacted (`***`). Test in Task 2/3.
4. `fetch_pages` result pointing at a private address or a robots-disallowed page → that page reports an error, search results still returned. Test in Task 4.
5. A slow provider or slow result pages → bounded by page timeout and a 60 s overall budget for `fetch_pages`. Test in Task 4.

---

### Task 1: Core fetcher additions

**Files:** Modify `src/pagemint/fetcher.py`, `src/pagemint/safety.py`. Test `tests/test_safety.py`.

**Produces:**
- `Fetcher.fetch(..., headers: dict | None = None, follow_redirects: bool = True)` and same params on `fetch_json`. With `follow_redirects=False` a 3xx raises `FetchError("... answered with a redirect ...; not followed")`.
- `safety.private_origin_allowed(origin: tuple[str, str, int])` — a context manager (contextvar) under which `resolve_and_check` (pre-check and the connect-time guard) allows private addresses for that exact `(scheme, host, port)` only; `safety.origin_of(url) -> tuple[str,str,int]`.

Tests: `test_custom_headers_sent`; `test_no_redirect_mode_raises_and_target_not_hit` (target path hits 0, custom header not sent to target); `test_private_origin_allowed_only_inside_context` with strict client: `http://127.0.0.1:{port}/x` blocked outside, allowed inside `private_origin_allowed(("http","127.0.0.1",port))`, while `http://localhost:{port}/x` (different host) and `http://127.0.0.1:{other_port}` stay blocked inside it.

Commit: `"Search step 1: request headers, no-redirect mode and scoped private-origin exception"`.

### Task 2: Settings and key redaction

**Files:** Modify `src/pagemint/config.py`. Create `src/pagemint/search/__init__.py`, `search/secrets.py`. Test `tests/search/test_config.py`.

**Produces:** Settings fields `searxng_url: str = ""`, `tavily_api_key: str = field(default="", repr=False)`, `brave_api_key: str = field(default="", repr=False)`, `search_provider: str = ""`, `search_fallback: bool = False` (all read by `from_env`). `secrets.redact(text: str, settings: Settings) -> str` replaces each non-empty key (len ≥ 4) with `***`.

Tests: env vars populate fields; `repr(settings)` contains neither key; `redact("token abc123secret in url", s)` → no key; empty key leaves text unchanged.

Commit: `"Search step 2: search settings and key redaction"`.

### Task 3: Providers

**Files:** Create `search/model.py`, `search/searxng.py`, `search/tavily.py`, `search/brave.py`. Test `tests/search/test_providers.py`.

**Produces:**
- `@dataclass SearchResult(title: str, url: str, snippet: str = "", published: str | None = None)`; `clean_results(raw: list[SearchResult], limit) -> list[SearchResult]` (normalize URL, http(s) only, dedupe, plain-text title/snippet via `jobs.model.html_to_text`, drop missing title/url, cap).
- Each provider: `async def search(client, settings, query: str, limit: int) -> list[SearchResult]`; module constant `BASE` (Tavily `https://api.tavily.com`, Brave `https://api.search.brave.com`); errors raise `PageMintError` subclasses with redacted messages.
- SearXNG: `GET {searxng_url}/search?q=&format=json` with `check_robots=False`, `follow_redirects=False`, inside `private_origin_allowed(origin_of(searxng_url))`; 403 → `FetchError` mentioning `formats: [html, json]`.
- Tavily: `POST {BASE}/search` JSON `{"query", "max_results": limit}`, header `Authorization: Bearer <key>`, no robots, no redirects.
- Brave: `GET {BASE}/res/v1/web/search?q=&count=` header `X-Subscription-Token: <key>`, `Accept: application/json`, no robots, no redirects; fields `web.results[].{title,url,description,page_age|age}`.

Tests per provider with the local server (tests patch `BASE`; Tavily/Brave tests use `allow_private_networks=True` client): request shape (method, path, query/body, header present), mapping incl. `published` date, `clean_results` drops bad/duplicate rows (Review Focus 2), 401 → error type `fetch` whose message has no key (Review Focus 3), 429 → error, SearXNG 403 hint (Review Focus 1), SearXNG on 127.0.0.1 works with a **strict** client (exception scoped), redirect from Tavily → error and key header never seen by the redirect target.

Commit: `"Search step 3: SearXNG, Tavily and Brave providers"`.

### Task 4: Search service, MCP tool, CLI

**Files:** Create `search/service.py`; modify `server.py`, `cli.py`. Test `tests/search/test_service.py`, extend `tests/test_server.py`, `tests/test_cli.py`.

**Produces:** `PROVIDERS = {"searxng": searxng, "tavily": tavily, "brave": brave}`; `configured(settings) -> list[str]` (in selection order); `async run_search(client, query, limit=10, fetch_pages=0, provider="auto") -> dict` with the spec's shape; `not_configured` error text (SearXNG Docker hint + key variable names); fallback rules; `fetch_pages` via `client.scrape` concurrently under a 60 s budget (each page `{"url","title","markdown"}` or `{"url","error"}`). MCP tool `search(query, limit=10, fetch_pages=0, provider="auto")`. CLI `pagemint search QUERY [--limit] [--fetch] [--provider] [--searxng-url] [--json]`.

Tests: nothing configured → `not_configured`; selection order; pinned by arg/env; unconfigured provider requested → `invalid_argument`; failure without fallback → error naming other configured providers; with fallback → next provider used + warning; pinned + fallback → no fallback; `fetch_pages=2` returns Markdown; private result URL / robots-disallowed result → page error, results kept (Review Focus 4); slow pages bounded (budget patched small) (Review Focus 5); key absent from any returned JSON and captured logs; MCP lists `search`; CLI prints `title — url`.

Commit: `"Search step 4: search service, MCP tool and CLI"`.

### Task 5: Docs

README "Web search" section per spec (two setup paths with SearXNG Docker steps incl. JSON format, key variables, Claude Code/Desktop env examples, what is never done, provider notes); update tool list intro and limits table. Verify full suite green.

Commit: `"Search step 5: web search docs"`.
