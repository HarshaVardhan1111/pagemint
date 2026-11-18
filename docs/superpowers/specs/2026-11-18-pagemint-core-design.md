# PageMint Core — Design

Date: 2026-11-18
Status: draft, awaiting approval (builder check done)
Scope: project 1 of 3 (core: scrape, crawl, map). Job search (project 2) and
web search (project 3) get their own designs later and build on this core.

## Intent and why

Harsha uses Firecrawl every day from AI agents (mostly scrape and crawl) and
keeps hitting its limits. PageMint is a free, open-source (MIT) replacement
that runs locally as an MCP server and CLI, turning web pages into clean
Markdown for agents. Later Harsha wants a paid product; the expected shape is
a hosted service built on the same core (convenience, scale), not a closed
fork of the code.

All code is written from scratch. Nothing is copied from Firecrawl or other
scrapers. Third-party libraries are allowed only with permissive licenses
(MIT, BSD, Apache 2.0).

## Goals

- `scrape`, `crawl`, `map` as MCP tools and as CLI commands, same behavior.
- Clean Markdown that keeps the content agents need: headings, lists, tables,
  code blocks, links. Navigation, footers, cookie banners, ads and scripts
  are removed.
- Polite and safe by default: robots.txt, per-domain rate limit, timeouts,
  size limits, SSRF protection.
- Works with zero setup for static pages (`uvx pagemint`); JavaScript
  rendering available after an opt-in install.
- Library-first layout so a hosted API (later) can wrap the same core.

## Non-goals (this project)

- Web search, job search (projects 2 and 3).
- LLM-based extraction, screenshots, PDFs, login/cookies, proxies, CAPTCHA
  solving, stealth/anti-bot evasion.
- Any paid service or API key. (Rule: ask Harsha before adding one.)

## Anti-goals

- Ignoring robots.txt or rate limits "to make it work".
- A tool that can be pointed at internal networks (SSRF).
- The server crashing or hanging on a bad page; every failure becomes a
  clear error result.
- Copying code from Firecrawl or other projects.

## Constraints

- Python 3.11+, `pyproject.toml`, installable with `pip install pagemint` and
  runnable with `uvx pagemint`.
- MCP Python SDK. Note: the user asked for "FastMCP". In `mcp` 2.x (current:
  2.3.0) FastMCP was renamed to `MCPServer` (`from mcp.server.mcpserver import
  MCPServer`); same idea, new name. We depend on `mcp>=2,<3`.
- Repo URL for the User-Agent: https://github.com/HarshaVardhan1111/pagemint
- Tests run without live internet, against a local HTTP server.

## Approaches considered (content extraction)

- **A. `trafilatura` (Apache 2.0)** for main text + Markdown. Fastest to
  build, but tuned for news/blog articles; on docs, product and job pages it
  often drops tables, lists and code, and its behavior is hard to tune.
- **B. Own cleaner + `markdownify` (MIT). — Chosen.** Our code strips
  boilerplate and picks the main region; markdownify converts it, with our
  tweaks. More work, but quality is under our control (it is what would be
  sold later) and it keeps docs and job pages complete, which project 2
  needs.
- C. Own HTML→Markdown converter too: rejected for now; markdownify is MIT,
  small, and good enough. We can replace it later behind the same function.

Harsha picked B.

## Dependencies and licenses

Runtime:
- `mcp` — MIT
- `httpx` — BSD-3-Clause
- `lxml` — BSD-3-Clause
- `beautifulsoup4` — MIT (also pulled in by markdownify)
- `markdownify` — MIT

Optional extra `pagemint[js]`:
- `playwright` — Apache 2.0 (user then runs `playwright install chromium`)

Dev/test:
- `pytest` — MIT
- `pytest-asyncio` — Apache 2.0

Standard library only for: CLI (`argparse`), robots.txt parsing (own parser,
see below), sitemap XML, IP checks (`ipaddress`, `socket`).

The README lists each library and its license. Transitive dependencies of
`mcp` (pydantic, starlette, etc.) are permissive; the README notes that the
list covers direct dependencies.

## Architecture

Package `pagemint/` (src layout: `src/pagemint/`):

- `config.py` — `Settings` dataclass: user agent, rate limit (default 1
  req/s per domain), connect/read timeouts (default 10s / 20s), max page size
  (default 5 MB), max redirects (5), `allow_private_networks` (default
  False), JS fallback threshold. Built from defaults, overridable by env
  vars (`PAGEMINT_*`) and CLI flags.
- `safety.py` — URL validation and SSRF guard (see Safety).
- `ratelimit.py` — per-domain async rate limiter.
- `robots.py` — own robots.txt parser + per-host cache.
- `fetcher.py` — httpx fetch with redirects handled manually (each hop
  re-checked by the SSRF guard and robots), size cap via streaming, content
  type check (HTML/XHTML/plain text; other types → clear error).
- `browser.py` — Playwright renderer (optional import).
- `extract.py` — boilerplate removal + main-region selection.
- `markdown.py` — HTML→Markdown via markdownify with our tweaks.
- `links.py` — link extraction, URL normalization (resolve relative, drop
  fragments, lowercase scheme/host, strip default ports).
- `scrape.py`, `crawl.py`, `sitemap.py` / `mapper.py` — the three operations.
- `server.py` — MCP server exposing the tools.
- `cli.py` — `pagemint` command.
- `errors.py` — `PageMintError` hierarchy with user-facing messages.

One `PageMint` client object owns the httpx client, rate limiter, robots
cache and settings; the MCP server and CLI each create one and share it
across calls.

## Tools

All tools return JSON-serializable dicts. Errors never raise out of a tool;
they come back as `{"error": {"type": ..., "message": ...}}` alongside the
URL (MCP tool result marked as error where the SDK supports it).

### scrape(url, format="markdown", render_js=False)

`format` is one of `markdown`, `html`, `links`.

Returns:
```
{
  "url": final URL after redirects,
  "status_code": int,
  "title": str | null,
  "content": str,        # markdown, cleaned main HTML, or "" for links
  "links": [str, ...],   # absolute, normalized, de-duplicated
  "rendered_js": bool,   # true if Playwright was used
  "warnings": [str, ...] # e.g. "JS fallback unavailable: playwright not installed"
}
```
- `html` format returns the cleaned main-content HTML (not raw page).
- `links` format returns `content=""` and the links list.
- Non-2xx responses still return status and whatever content exists, with a
  warning; network/safety failures return an error.

### crawl(url, max_pages=20, max_depth=2, same_domain=True)

Breadth-first from `url`. Depth 0 = start page. Follows links found in each
page; skips non-HTML links by extension (images, pdf, zip…), already-seen
URLs (after normalization), and URLs blocked by robots/SSRF.
`same_domain=True` means same host (www. treated as the same host).
Hard caps: `max_pages` ≤ 100, `max_depth` ≤ 5 (values above are clamped
with a warning).

Returns:
```
{
  "start_url": str,
  "pages": [{"url", "status_code", "title", "markdown", "depth",
             "truncated": bool, "rendered_js": bool}],
  "skipped": [{"url", "reason"}],   # robots, ssrf, error, limit
  "stopped_early": bool,            # time budget or output budget hit
  "warnings": [...]
}
```

Size and time (so agents don't time out or get cut off):
- Synchronous call with a total time budget (default 120s, setting
  `crawl_time_budget`). When it runs out, the crawl stops and returns the
  pages so far with `stopped_early=true` and a warning.
- Output budget: each page's Markdown is capped at 20,000 characters
  (`truncated=true` when cut), and the whole result at 300,000 characters;
  pages beyond that are listed in `skipped` with reason `output budget`.
- Progress notifications are sent to the MCP client per page where the SDK
  supports it.
- The CLI has `--out DIR` to write one `.md` file per page with no caps.
- JS fallback: the same auto-fallback as scrape applies to each thin page,
  and `crawl` takes `render_js=False` to force rendering for every page.
  Rendering is one page at a time, goes through the same rate limiter, and
  counts against the time budget.

### map(url, limit=500)

1. Read robots.txt `Sitemap:` lines; else try `/sitemap.xml`.
2. Parse sitemaps (urlset and sitemapindex, nested up to 3 levels, gzip
   supported); collect URLs on the same host.
3. If fewer than `limit` URLs, add links from the start page (one fetch, no
   deep crawl).
Hard cap: `limit` ≤ 5000. Returns `{"url", "urls": [...], "source":
"sitemap" | "links" | "sitemap+links", "warnings": [...]}`.
Map does not fetch every URL, so robots.txt filtering applies to the URLs
returned (disallowed URLs are left out and counted in a warning).

## Content extraction (approach B)

1. Parse with lxml.
2. Remove: `script`, `style`, `noscript`, `iframe`, `svg`, `canvas`,
   `form` controls, `template`, hidden elements (`hidden` attr,
   `aria-hidden="true"`, inline `display:none`).
3. Remove boilerplate by tag and role: `nav`, `header`, `footer`, `aside`,
   `[role=navigation|banner|contentinfo|complementary]`, and elements whose
   id/class matches a word list (cookie, consent, banner, advert, ads,
   promo, newsletter, subscribe, share, social, sidebar, breadcrumb, popup,
   modal, related, comments). Word matching is on whole tokens, and an
   element is never removed if it contains most of the page's text (guards
   against `class="content-sidebar-layout"` wrappers).
4. Pick the main region: `<main>` or `[role=main]` if present and it holds a
   reasonable share of text; else a single `<article>`; else the block with
   the best text-density score (text length, minus link-text ratio penalty,
   bonus for `p`, `pre`, `table`, `li` content). Fallback: whole `<body>`.
5. Title: `og:title`, else `<title>`, else first `<h1>`.
6. Convert with markdownify (ATX headings, `-` bullets), with tweaks:
   fenced code blocks with language from `class="language-x"`, GitHub-style
   tables, absolute link and image URLs, drop empty links, collapse 3+ blank
   lines.

## JS rendering and fallback

- `render_js=True` → render with Playwright Chromium.
- Auto-fallback: after a plain fetch returns 2xx HTML, if the extracted
  Markdown has fewer than 200 visible characters (configurable), re-fetch
  with Playwright and use that result if it is longer.
- If Playwright or Chromium is missing, return the plain result plus a
  warning explaining how to install (`pip install "pagemint[js]"` then
  `playwright install chromium`). Never an error just for this.
- Browser safety: every request the page makes goes through Playwright
  route interception and the same SSRF guard; blocked requests are aborted.
  Downloads disabled. Navigation timeout = read timeout. Wait for `load`,
  then up to 2s of network idle.
- One browser instance per `PageMint` client, launched lazily, closed on
  shutdown.

## Safety and politeness

- **Schemes:** only `http` and `https`; anything else → error.
- **SSRF:** resolve the host (all A/AAAA records); reject if any address is
  loopback, private, link-local, multicast, reserved, unspecified, CGNAT
  (100.64/10), or IPv4-mapped equivalents; also reject `localhost` names and
  bare integer/hex IPs that resolve that way. Checked for the first URL and
  every redirect hop and every browser subrequest. To reduce DNS-rebinding
  risk, httpx connects to the address we validated (custom transport that
  pins the resolved IP while keeping the Host header and TLS SNI).
  `allow_private_networks=True` (env `PAGEMINT_ALLOW_PRIVATE=1`, CLI
  `--allow-private`) disables this — used by the tests and by people
  scraping their own intranet on purpose.
- **robots.txt:** fetched once per host (cached for the client lifetime,
  1 hour max), parsed by our own parser: groups by user-agent, matches
  `PageMint` token else `*`, supports `Allow`, `Disallow`, `*` and `$`
  wildcards, longest-match wins, `Allow` wins ties, `Crawl-delay` respected
  if larger than our rate limit, `Sitemap:` lines collected. 4xx on
  robots.txt → allow all; 5xx or network error → disallow all for that host
  (conservative, as RFC 9309 says). Disallowed URL → error/skip with reason
  "Blocked by robots.txt (rule: Disallow: /path)".
- **Rate limit:** per host, default 1 request/second, applies to robots,
  sitemaps, pages and the browser's top-level navigation (not to the
  browser's subresources). Async-safe.
- **Timeouts:** connect 10s, read 20s, total per page 30s.
- **Size:** streamed; abort at 5 MB with a clear error.
- **User-Agent:** `PageMint/<version> (+https://github.com/HarshaVardhan1111/pagemint)`.
- **No crash:** every tool wraps its work; unexpected exceptions become
  `{"error": {"type": "internal", "message": ...}}` and are logged to stderr
  (never stdout, which MCP stdio uses).

## CLI

```
pagemint                    # no args: run MCP server on stdio (for uvx)
pagemint serve              # same, explicit
pagemint scrape <url> [--format markdown|html|links] [--js] [--json]
pagemint crawl <url> [--max-pages N] [--max-depth N] [--any-domain] [--json]
pagemint map <url> [--limit N] [--json]
Common: --rate-limit SECONDS, --timeout SECONDS, --allow-private
```
Default output: Markdown/plain text to stdout; `--json` prints the full
result. Errors go to stderr with exit code 1.

## Testing

pytest + pytest-asyncio. A fixture starts a local `http.server`-based server
in a thread (stdlib, no extra dep) on 127.0.0.1 with a route table, and the
client is created with `allow_private_networks=True`. Tests cover:
- scrape: title, Markdown keeps headings/table/code/links, boilerplate gone,
  formats html/links, non-HTML content type error, size limit, timeout.
- crawl: max_pages, max_depth, same_domain, dedupe, robots skips listed.
- map: sitemap, sitemap index, gzip, fallback to links, limit.
- robots parser: unit tests for wildcards, `$`, precedence, agent groups,
  4xx/5xx behavior.
- rate limit: N requests to one host take ≥ (N-1) × interval; two hosts run
  in parallel.
- SSRF: with protection on, localhost/127.0.0.1/10.x/169.254.169.254/[::1],
  redirects into private space, and non-http schemes are blocked.
- JS fallback: a page that builds its content with JavaScript. Test runs
  only if Playwright + Chromium are available (skipped otherwise, and CI
  installs them so it runs there). A unit test with a fake renderer covers
  the fallback decision logic without a browser.
- MCP: tools are registered and return the documented shape (in-process).

## CI

GitHub Actions on every push and PR: Python 3.11, 3.12, 3.13 on
ubuntu-latest; install `.[js,dev]`, `playwright install --with-deps
chromium`, run pytest.

## Releasing to PyPI

`pip install pagemint` and `uvx pagemint` only work once the package is on
PyPI (the name was free when checked on 2026-11-18). The repo gets a release
workflow (`.github/workflows/release.yml`) that builds and publishes on a
`v*` tag using PyPI trusted publishing, so no API token is stored anywhere.
It does nothing until Harsha creates a free PyPI account and adds the repo as
a trusted publisher; the README explains the steps. Until then the README
also shows install-from-GitHub:
`uvx --from git+https://github.com/HarshaVardhan1111/pagemint pagemint`.

## Docs and repo files

README (what, install, Claude Code: `claude mcp add pagemint -- uvx
pagemint`, Claude Desktop JSON config, JS extra, tool reference, limits and
defaults, safety behavior, license list), CONTRIBUTING.md, LICENSE (MIT,
copyright Harsha), `.gitignore`.

## Build order (from the request)

1. Project skeleton + scrape
2. Crawl + map
3. Safety rules (robots, rate limit, SSRF, limits)
4. Playwright fallback
5. CLI
6. Docs + CI

Tests run after each step; one commit per step with a clear message.
Note: the structure for safety (settings, client object) exists from step 1
so step 3 adds behavior, not a rewrite.

## Left to the builder

Internal function names, exact word lists and score weights (tune against
test pages), log format, exact error type names.

## Decisions made by Claude (check these)

- `mcp>=2,<3` with `MCPServer` (FastMCP's new name in the official SDK;
  not the separate third-party `fastmcp` package, since the request said
  "official MCP Python SDK").
- Crawl: 120s time budget, 20k chars per page, 300k chars per result,
  partial results when a budget is hit; CLI `--out DIR` has no caps.
- Crawl uses the JS auto-fallback too, plus a `render_js` option.
- Release workflow via PyPI trusted publishing, ready but inactive until
  Harsha sets up PyPI.
- Own robots.txt parser instead of stdlib `urllib.robotparser` (stdlib does
  not support `*`/`$` wildcards).
- robots.txt 5xx → disallow host; 4xx → allow all.
- Hard caps: crawl ≤ 100 pages / depth ≤ 5; map ≤ 5000 URLs; 5 MB pages.
- JS fallback threshold: < 200 characters of extracted text.
- `html` format returns cleaned main-content HTML, not the raw page.
- argparse for the CLI (no extra dependency).
