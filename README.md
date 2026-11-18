# PageMint

**Free, open-source MCP server that turns web pages into clean Markdown for AI agents.**

PageMint gives Claude (and any other MCP client) three tools: `scrape` one page, `crawl` a site, and `map` a site's URLs. It runs on your own machine, so there are no credits, API keys or monthly limits. It is polite by default: it follows robots.txt, rate-limits every site, and refuses to touch private networks.

- Clean Markdown: menus, footers, cookie banners and ads are removed; headings, lists, tables, code blocks and links are kept.
- JavaScript pages: if a page is almost empty without JavaScript, PageMint renders it in headless Chromium (optional install).
- Written from scratch in Python. MIT license.

## Install

PageMint needs Python 3.11 or newer.

```bash
pip install pagemint
```

Or run it without installing, using [uv](https://docs.astral.sh/uv/):

```bash
uvx pagemint --help
```

> **Before the first PyPI release**, install from GitHub instead:
> `uvx --from git+https://github.com/HarshaVardhan1111/pagemint pagemint --help`
> (and use the same `--from ...` in the setup commands below).

### Optional: JavaScript rendering

Plain pages work out of the box. For sites that build their content with JavaScript, add the browser extra and download Chromium once (about 150 MB):

```bash
pip install "pagemint[js]"
playwright install chromium
```

With uv:

```bash
uvx --from "pagemint[js]" playwright install chromium
```

Without the browser, PageMint still works and tells you when a page needed JavaScript.

## Add it to Claude Code

```bash
claude mcp add pagemint -- uvx pagemint
```

With JavaScript rendering:

```bash
claude mcp add pagemint -- uvx --from "pagemint[js]" pagemint
```

## Add it to Claude Desktop

Open **Settings → Developer → Edit Config** (the file is `claude_desktop_config.json`; on macOS it lives in `~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`) and add:

```json
{
  "mcpServers": {
    "pagemint": {
      "command": "uvx",
      "args": ["pagemint"]
    }
  }
}
```

For JavaScript rendering use `"args": ["--from", "pagemint[js]", "pagemint"]`. Restart Claude Desktop afterwards.

## Tools

### `scrape(url, format="markdown", render_js=False)`

Fetches one page.

| Field | Meaning |
|---|---|
| `url` | Final URL after redirects |
| `status_code` | HTTP status code |
| `title` | Page title (`og:title`, then `<title>`, then the first `<h1>`) |
| `content` | Markdown (default), cleaned main-content HTML (`format="html"`), or empty (`format="links"`) |
| `links` | Every http(s) link on the page, absolute and de-duplicated |
| `rendered_js` | `true` if the page was rendered in the browser |
| `warnings` | Things worth knowing, e.g. a 404 status or a missing browser |

`render_js=True` always uses the browser. Without it, PageMint uses the browser automatically only when the plain page has under 200 characters of text.

### `crawl(url, max_pages=20, max_depth=2, same_domain=True, render_js=False)`

Follows links breadth-first from `url` and returns each page as Markdown.

- `max_depth` counts link hops from the start page (the start page is depth 0).
- `same_domain=True` stays on the start host (`www.` is treated as the same site).
- Files such as images, PDFs and archives are not followed.
- Returns `pages` (url, status_code, title, markdown, depth, truncated, rendered_js), `skipped` (url + reason: robots.txt, blocked, error, limit), `stopped_early` and `warnings`.

### `map(url, limit=500)`

Lists URLs on a site without fetching every page: it reads the sitemap first (from robots.txt `Sitemap:` lines, otherwise `/sitemap.xml`, including sitemap indexes and `.gz` files), then adds links from the start page if needed. URLs that robots.txt disallows are left out. Returns `urls`, `source` (`sitemap`, `links` or `sitemap+links`) and `warnings`.

Errors never crash the server. A failed call returns `{"error": {"type": ..., "message": ...}}` with a plain-English message and is marked as an error to the client.

## Command line

```bash
pagemint scrape https://example.com
```

```bash
pagemint scrape https://example.com --format links
```

```bash
pagemint crawl https://example.com/docs --max-pages 50 --out ./docs-md
```

```bash
pagemint map https://example.com --limit 1000
```

- `pagemint` with no command (or `pagemint serve`) runs the MCP server on stdio.
- `--json` prints the full result.
- `--js` renders with the browser.
- `crawl --out DIR` writes one `.md` file per page, with no size limits.
- Common options: `--rate-limit SECONDS`, `--timeout SECONDS`, `--allow-private`, `--browser PATH`.

## Limits and defaults

| Setting | Default | Environment variable |
|---|---|---|
| Delay between requests to the same site | 1 second | `PAGEMINT_RATE_LIMIT` |
| Connect / read timeout | 10 s / 20 s | `PAGEMINT_CONNECT_TIMEOUT`, `PAGEMINT_READ_TIMEOUT` |
| Total time per page (all redirects) | 30 s | `PAGEMINT_PAGE_TIMEOUT` |
| Maximum page size | 5 MB | `PAGEMINT_MAX_BYTES` |
| Maximum redirects | 5 | `PAGEMINT_MAX_REDIRECTS` |
| Browser fallback below | 200 characters of text | `PAGEMINT_JS_FALLBACK_CHARS` (0 turns it off) |
| Crawl time budget | 120 s | `PAGEMINT_CRAWL_TIME_BUDGET` |
| Crawl output per page / per call | 20,000 / 300,000 characters | `PAGEMINT_PAGE_CHAR_CAP`, `PAGEMINT_RESULT_CHAR_CAP` |
| Allow private networks | off | `PAGEMINT_ALLOW_PRIVATE=1` |
| Chrome/Chromium binary | Playwright's | `PAGEMINT_BROWSER_EXECUTABLE` |

Hard limits that cannot be raised: `crawl` at most 100 pages and depth 5; `map` at most 5,000 URLs.

When a crawl hits its time or output budget it stops and returns the pages it has, with `stopped_early: true`. Use the CLI with `--out` to save larger crawls to disk.

## Safety and politeness

- **robots.txt is always respected.** PageMint looks for rules for `PageMint`, then `*`, and supports `Allow`, `Disallow`, `*` and `$` wildcards and `Crawl-delay`. A blocked URL comes back with the rule that blocked it. If robots.txt is missing (4xx), everything is allowed. If it errors (5xx) or times out, the site is treated as off-limits for a minute.
- **Rate limit per site:** one request per second by default, or slower if `Crawl-delay` asks for it.
- **User-Agent:** `PageMint/<version> (+https://github.com/HarshaVardhan1111/pagemint)`, so site owners can see who is visiting.
- **SSRF protection:** only `http` and `https` URLs are fetched. Localhost, private ranges (10/8, 172.16/12, 192.168/16), link-local addresses (including cloud metadata at 169.254.169.254), CGNAT, multicast and IPv6 equivalents are refused. The check runs on every redirect hop, and for direct connections again when the connection opens, which stops DNS-rebinding tricks. In the browser, every request and WebSocket the page opens is checked too, and PageMint follows redirects itself so each hop is checked.
- **Behind a proxy:** if `HTTPS_PROXY` / `HTTP_PROXY` are set, PageMint uses them. When a name can only be resolved by the proxy, PageMint lets the proxy resolve it. Literal private IPs, localhost, single-word names and internal suffixes (`.internal`, `.local`, `.lan`, `.corp`, `.home.arpa`) are still refused.
- **Known limits:** DNS-rebinding protection at connect time covers direct connections only. Requests sent through a proxy, and requests made by the browser during JavaScript rendering, are checked before they are sent but not at the moment of connecting. If you scrape untrusted sites from a machine with sensitive internal services, run PageMint without a proxy and without JavaScript rendering, or in an isolated network.
- **Size and time limits** on every request, so one bad page cannot hang or flood your agent.

To scrape your own intranet or a local dev server on purpose, set `PAGEMINT_ALLOW_PRIVATE=1` or pass `--allow-private`.

## Libraries and licenses

PageMint uses only permissively licensed libraries.

| Library | Used for | License |
|---|---|---|
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | MCP server (`MCPServer`, formerly FastMCP) | MIT |
| [httpx](https://github.com/encode/httpx) | HTTP client | BSD-3-Clause |
| [httpcore](https://github.com/encode/httpcore) | Connection-level SSRF check | BSD-3-Clause |
| [lxml](https://lxml.de/) | HTML and sitemap parsing | BSD-3-Clause |
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) | HTML tree for Markdown conversion | MIT |
| [markdownify](https://github.com/matthewwithanm/python-markdownify) | HTML to Markdown | MIT |
| [playwright](https://github.com/microsoft/playwright-python) (optional `[js]`) | JavaScript rendering | Apache-2.0 |
| [pytest](https://pytest.org/) (development) | Tests | MIT |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) (development) | Async tests | Apache-2.0 |

These pull in further dependencies, almost all MIT, BSD or Apache-2.0. Two exceptions:

- `certifi` (CA certificates, required by httpx) is **MPL-2.0**.
- `typing_extensions` (and part of `greenlet`) are **PSF-2.0**.

Both are used unmodified, which is compatible with MIT.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests run entirely against a local HTTP server, with no internet access needed:

```bash
pip install -e ".[js,dev]"
```

```bash
playwright install chromium
```

```bash
pytest
```

## Releasing (maintainers)

Pushing a tag like `v0.1.0` builds the package and publishes it to PyPI through [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored anywhere. One-time setup on pypi.org: create a free account, then under **Publishing** add a pending publisher with owner `HarshaVardhan1111`, repository `pagemint`, workflow `release.yml` and environment `pypi`.

## License

MIT. See [LICENSE](LICENSE).
