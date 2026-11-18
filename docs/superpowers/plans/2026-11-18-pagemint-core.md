# PageMint Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pip/uvx-installable MCP server + CLI with `scrape`, `crawl`, `map` that turns web pages into clean Markdown, safely and politely.

**Architecture:** Library-first `src/pagemint/` package. One `PageMint` client owns settings, the httpx client, rate limiter, robots cache and (lazily) a browser. Operations (`scrape`, `crawl`, `map_site`) are async functions on that client; `server.py` (MCP) and `cli.py` are thin wrappers.

**Tech Stack:** Python 3.11+, mcp>=2,<3 (`MCPServer`), httpx, lxml, beautifulsoup4, markdownify, optional playwright; pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-18-pagemint-core-design.md`

## Global Constraints

- Python `>=3.11`; MIT license; only MIT/BSD/Apache-2.0 dependencies.
- User-Agent exactly: `PageMint/<version> (+https://github.com/HarshaVardhan1111/pagemint)`.
- Defaults: rate 1 req/s per host; connect 10s, read 20s, per-page total 30s; max page 5 MB; max redirects 5; JS fallback below 200 chars.
- Caps: crawl max_pages ≤ 100, max_depth ≤ 5; crawl time budget 120s; 20,000 chars/page, 300,000 chars/result; map limit ≤ 5000.
- `allow_private_networks` default False; env `PAGEMINT_ALLOW_PRIVATE=1`; CLI `--allow-private`.
- Tools never raise: errors return `{"error": {"type", "message"}}`. Logs to stderr only.
- No live internet in tests; local server on 127.0.0.1.
- One commit per build step with a clear message.

## Review Focus

1. Non-UTF-8 / missing charset pages (e.g. `windows-1252`, charset only in `<meta>`) → decoded correctly, no mojibake. Test in Task 1.
2. Relative links with `<base href>`, `#fragments`, `mailto:`/`javascript:` links → absolute http(s) only, fragments dropped, others ignored. Test in Task 1.
3. Redirect from a public URL to a private IP / to a robots-disallowed path → blocked at the hop. Test in Task 3.
4. Server returns a body with no Content-Length larger than 5 MB → aborted mid-stream with size error. Test in Task 3.
5. robots.txt that times out or returns 503 → host treated as disallowed with a clear reason, not a crash. Test in Task 3.

---

## File map

```
pyproject.toml, LICENSE, README.md, CONTRIBUTING.md, .gitignore
src/pagemint/__init__.py      __version__ = "0.1.0"
src/pagemint/config.py        Settings
src/pagemint/errors.py        PageMintError + subclasses, error_dict()
src/pagemint/links.py         normalize_url, extract_links, same_site
src/pagemint/extract.py       extract_main(html, url) -> Extracted
src/pagemint/markdown.py      to_markdown(html_fragment, base_url) -> str
src/pagemint/safety.py        validate_url, check_host, is_blocked_ip
src/pagemint/ratelimit.py     RateLimiter
src/pagemint/robots.py        RobotsRules, parse_robots, RobotsCache
src/pagemint/fetcher.py       Fetcher.fetch(url) -> FetchResult
src/pagemint/browser.py       BrowserRenderer
src/pagemint/sitemap.py       parse_sitemap
src/pagemint/client.py        PageMint (scrape, crawl, map_site, aclose)
src/pagemint/server.py        build_server(), main()
src/pagemint/cli.py           main(argv)
tests/conftest.py             local_server fixture, client fixture
tests/test_*.py
.github/workflows/ci.yml, release.yml
```

### Task 1: Skeleton + scrape (build step 1)

**Interfaces — produces:**
- `Settings(user_agent:str, rate_limit:float=1.0, connect_timeout:float=10, read_timeout:float=20, page_timeout:float=30, max_bytes:int=5_000_000, max_redirects:int=5, allow_private_networks:bool=False, js_fallback_chars:int=200, crawl_time_budget:float=120, page_char_cap:int=20_000, result_char_cap:int=300_000)`; `Settings.from_env(**overrides)`.
- `errors.PageMintError(message)` with `.type: str`; subclasses `InvalidURLError("invalid_url")`, `BlockedError("blocked")`, `RobotsError("robots")`, `FetchError("fetch")`, `TooLargeError("too_large")`, `ContentTypeError("content_type")`, `TimeoutError_("timeout")`; `error_dict(exc) -> {"error": {"type", "message"}}` (non-PageMint → type `internal`).
- `links.normalize_url(url:str, base:str|None=None) -> str|None` (None for non-http(s)); `links.extract_links(html:str, base_url:str) -> list[str]` (honours `<base href>`, dedupe, order kept).
- `extract.extract_main(html:str, url:str) -> Extracted(title:str|None, main_html:str, text_len:int)`.
- `markdown.to_markdown(html:str, base_url:str) -> str`.
- `fetcher.FetchResult(url, status_code, headers, text, content_type)`; `Fetcher(settings).fetch(url) -> FetchResult` (step 1: plain httpx with UA, timeouts, charset decode).
- `client.PageMint(settings: Settings|None=None)`; `async scrape(url, format="markdown", render_js=False) -> dict` (spec shape); `async aclose()`; async context manager.
- `server.build_server(client_factory=PageMint) -> MCPServer`; tool `scrape` registered.

Tests (`tests/test_scrape.py`, `tests/test_extract.py`, `tests/test_links.py`):
- `test_scrape_markdown_keeps_structure`: fixture page with nav, footer, cookie banner, `<main>` holding h1, table, `<pre><code class="language-python">`, link → content contains `# Title`, `| a | b |`, "```python", absolute link; does not contain nav/footer/cookie text; `status_code == 200`; `title` from `<title>`.
- `test_scrape_formats`: `html` content contains `<table`, no `<nav`; `links` → `content == ""` and links absolute.
- `test_scrape_non_html_returns_error`: `application/pdf` → `result["error"]["type"] == "content_type"`.
- `test_decodes_meta_charset` (Review Focus 1): bytes encoded cp1252 with `<meta charset="windows-1252">` and "café" → "café" in content.
- `test_links_base_and_fragments` (Review Focus 2): `<base href="/docs/">`, `a.html#x`, `mailto:`, `javascript:` → `["http://h/docs/a.html"]`.
- `test_extract_density_fallback`: no `<main>`/`<article>`, a big text div + link-heavy sidebar div → main_html contains big text, not sidebar.
- `test_mcp_tools_registered`: server lists tool `scrape` (later tasks add `crawl`, `map`).

Steps: write tests → run (fail) → implement → run (pass) → `git commit -m "Step 1: project skeleton and scrape tool"`.

### Task 2: Crawl + map (build step 2)

**Interfaces — consumes:** Task 1. **Produces:**
- `PageMint.crawl(url, max_pages=20, max_depth=2, same_domain=True, render_js=False) -> dict` (spec shape incl. `stopped_early`, `truncated`).
- `PageMint.map_site(url, limit=500) -> dict` (MCP tool name `map`).
- `sitemap.parse_sitemap(content: bytes) -> tuple[list[str] urls, list[str] child_sitemaps]` (handles gzip magic bytes).
- `links.same_site(a, b) -> bool` (host equal ignoring leading `www.`).

Tests (`tests/test_crawl.py`, `tests/test_map.py`):
- `test_crawl_respects_max_pages` (site of 30 linked pages, max_pages=5 → 5 pages).
- `test_crawl_respects_max_depth` (chain a→b→c→d, max_depth=2 → depths {0,1,2}).
- `test_crawl_same_domain` (link to other host `127.0.0.2`-style alt name → in skipped or not followed).
- `test_crawl_dedupes_fragments_and_trailing` (`/a` and `/a#x` → one page).
- `test_crawl_clamps_caps` (max_pages=500 → warning mentions 100).
- `test_crawl_page_char_cap` (60k-char page → `truncated` true, len ≤ 20,000).
- `test_crawl_time_budget` (settings budget 0.5s, rate 0.3s → `stopped_early` true).
- `test_map_from_sitemap`, `test_map_sitemap_index_gzip`, `test_map_falls_back_to_links`, `test_map_limit`.
- MCP: tools `crawl` and `map` registered.

Commit: `"Step 2: crawl and map tools"`.

### Task 3: Safety rules (build step 3)

**Produces:**
- `safety.is_blocked_ip(ip: ipaddress.IPv4Address|IPv6Address) -> bool` (loopback, private, link-local, multicast, reserved, unspecified, 100.64.0.0/10, IPv4-mapped).
- `safety.validate_url(url) -> str` raises `InvalidURLError` (scheme/host).
- `async safety.resolve_and_check(host, port, allow_private) -> list[str]` raises `BlockedError`.
- Fetcher: manual redirects (each hop: validate, SSRF check, robots check), streaming size cap, IP pinning transport (connect to validated IP; Host header + SNI unchanged).
- `ratelimit.RateLimiter(interval: float)`; `async wait(host: str)`; `set_interval(host, seconds)` for Crawl-delay.
- `robots.parse_robots(text) -> RobotsFile`; `RobotsFile.rules_for(agent) -> RobotsRules`; `RobotsRules.check(path_with_query) -> tuple[bool, str|None rule]`; `.crawl_delay`; `RobotsFile.sitemaps`. `RobotsCache.allowed(url) -> tuple[bool, str reason]` (4xx allow all; 5xx/network → disallow).

Tests (`tests/test_safety.py`, `tests/test_robots.py`, `tests/test_ratelimit.py`):
- SSRF with `allow_private_networks=False`: `http://localhost/`, `http://127.0.0.1/`, `http://10.0.0.1/`, `http://169.254.169.254/`, `http://[::1]/`, `http://2130706433/`, `ftp://x`, `file:///etc/passwd` → error types `blocked`/`invalid_url`.
- `test_redirect_to_private_blocked` (Review Focus 3): fake resolver maps `public.test` → 93.184.216.34 via a monkeypatched resolver + MockTransport-free approach: unit-test `Fetcher` hop check with a patched `resolve_and_check` that blocks the second hop → `blocked`.
- `test_redirect_to_disallowed_path_blocked`.
- `test_size_limit_chunked` (Review Focus 4): chunked 6 MB body, max_bytes 5 MB → `too_large`.
- robots parser: wildcard `*`, `$`, longest match, Allow wins tie, agent group selection (`PageMint` vs `*`), case-insensitive agent, Crawl-delay, Sitemap lines.
- `test_robots_5xx_disallows`, `test_robots_404_allows`, `test_robots_timeout_disallows` (Review Focus 5).
- `test_scrape_disallowed_reports_rule` → message contains `robots.txt` and the rule.
- `test_crawl_skips_disallowed_with_reason`.
- `test_rate_limit_same_host` (3 requests, interval 0.3 → ≥ 0.6s); `test_rate_limit_hosts_independent`.
- `test_user_agent_header` (server records UA == `PageMint/0.1.0 (+https://github.com/HarshaVardhan1111/pagemint)`).

Commit: `"Step 3: robots.txt, rate limiting, SSRF protection and limits"`.

### Task 4: Playwright fallback (build step 4)

**Produces:** `browser.BrowserRenderer(settings, ssrf_check)`; `async render(url) -> FetchResult`; `async aclose()`; raises `BrowserUnavailableError` when playwright/chromium missing. `PageMint(settings, renderer=None)` accepts an injected renderer.

Tests (`tests/test_js.py`):
- `test_fallback_used_when_thin` (fake renderer returns rich HTML → `rendered_js` true).
- `test_fallback_not_used_when_rich`.
- `test_fallback_unavailable_warns` (renderer raising unavailable → plain result + warning containing `playwright install chromium`).
- `test_real_browser_renders_js_page` (skip if playwright/chromium missing): page building content with JS → content contains it.
- `test_browser_blocks_private_subrequests` (skip as above): with protection on and a public-looking hostname mapped... implemented as unit test of the route handler calling the check.

Commit: `"Step 4: Playwright JavaScript rendering with automatic fallback"`.

### Task 5: CLI (build step 5)

**Produces:** `cli.main(argv: list[str]|None=None) -> int`; console script `pagemint = "pagemint.cli:main"`. No args/`serve` → MCP stdio.

Tests (`tests/test_cli.py`): `scrape` prints Markdown; `--json` prints parseable JSON with `status_code`; `crawl --out DIR` writes `.md` files; `map` prints one URL per line; error → exit 1, message on stderr.

Commit: `"Step 5: pagemint command-line interface"`.

### Task 6: Docs + CI (build step 6)

README (sections per spec, license table), CONTRIBUTING.md, LICENSE (MIT, "Copyright (c) 2026 Harsha"), `.github/workflows/ci.yml` (3.11/3.12/3.13, `pip install -e ".[js,dev]"`, `playwright install --with-deps chromium`, `pytest`), `release.yml` (tag `v*`, build, `pypa/gh-action-pypi-publish` with `id-token: write`). Verify: `python -m build` succeeds and `uvx --from . pagemint --help` works.

Commit: `"Step 6: README, contributing guide, CI and release workflows"`.
