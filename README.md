# PageMint

<!-- mcp-name: io.github.HarshaVardhan1111/pagemint -->
<!-- mcp-name: io.github.harshavardhan1111/pagemint -->

**Free, open-source MCP server that turns web pages into clean Markdown for AI agents.**

PageMint gives Claude (and any other MCP client) tools to `scrape` one page, `crawl` a site, `map` a site's URLs, **search open jobs** straight from companies' own hiring systems, and **search the web** with your own SearXNG or your own search API key. It runs on your own machine, so scraping, crawling and job search have no credits, API keys or monthly limits. (Web search needs your own SearXNG or your own search API key; see below.) It is polite by default: it follows robots.txt, rate-limits every site, and refuses to touch private networks.

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

## Hosted PageMint (claude.ai web and phone)

claude.ai in the browser and the Claude phone app can only use MCP servers on the internet. PageMint can run as one:

- `pagemint serve --http` serves the same tools over Streamable HTTP at `/mcp`.
- In **hosted mode** (`PAGEMINT_HOSTED=1`) people sign in with GitHub. PageMint never stores passwords or GitHub tokens; its own tokens are stored only as hashes.
- Each person gets their own saved company list and daily limits: 200 pages, 100 job searches, 50 web searches and 30 JavaScript page renders. There are also server-wide caps, and requests to any one website stay at one per second for the whole server.
- Logs keep only the time, user id, tool, website host name, result and duration. They never keep page contents, full links or search words, and are deleted after 14 days.

To add a hosted PageMint to Claude: claude.ai → **Settings → Connectors → Add custom connector** → `https://YOUR-SERVER/mcp` → sign in with GitHub. Once added on the web, it also works on the phone.

To run your own for free on Oracle Cloud's Always Free tier (Docker, automatic HTTPS, SearXNG for web search, nightly backups, automatic deploys from GitHub releases), follow [deploy/README.md](deploy/README.md).

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

## Job search

Four more tools find **open** jobs straight from the systems companies use to post them, so you don't see closed jobs that linger on job boards.

| Tool | What it does |
|---|---|
| `company_jobs(company, query="", location="", remote=None, limit=50)` | Open jobs at one company. `company` is a name (`"Stripe"`) or a careers-page / job-board URL. The company is added to your saved list. |
| `search_jobs(query, location="", remote=None, posted_within_days=None, companies=None, sources="all", limit=25)` | Searches all saved companies (or the `companies` you pass) plus the free job sources below, best matches first. `sources="companies"` skips the free sources. Max 100 results. |
| `check_job(url)` | Is this job still open? Returns `open`, `closed` or `unknown` with the reason, and the full description when open. |
| `saved_companies(action="list", company=None)` | List, `add` or `remove` saved companies. |

Each job has title, company, location, remote / workplace type, employment type, department, posted date, salary (when published), links, a short description, its `source` and a `status`: `open` for jobs read from a company's hiring system, `unverified` for jobs from free sources (run `check_job` on them).

### Where jobs come from

| Source | How | Notes |
|---|---|---|
| Greenhouse | public Job Board API | company name is confirmed from the feed |
| Lever | public Postings API (global and EU) | |
| Ashby | public job posting API | |
| Workday | the career site's own JSON endpoints | needs the careers URL (e.g. `https://acme.wd5.myworkdayjobs.com/External`) |
| Recruitee | public offers API | experimental |
| Any other careers page | schema.org `JobPosting` data that job pages carry for search engines | follows up to 30 job links; expired jobs are dropped |
| [Hacker News "Who is hiring?"](https://news.ycombinator.com/submitted?id=whoishiring) | HN Algolia API, current and previous month | free text, parsed best effort |
| [Arbeitnow](https://www.arbeitnow.com) | free job board API | mostly Europe; credited as its terms ask |
| [Himalayas](https://himalayas.app) | free remote jobs API | rate-limits quickly; skipped with a note when it does |

**Considered and not used:** LinkedIn, Indeed, Glassdoor and ZipRecruiter (their terms forbid scraping or they block bots), SmartRecruiters (its robots.txt blocks all bots except LinkedIn's), Remotive (robots.txt blocks its API), Workable search (rate-limited, and search URLs are disallowed). Workable careers pages are still read through the general reader when they allow it.

### How finding a company works

- **By URL** (most reliable): PageMint recognises Greenhouse, Lever, Ashby, Workday, Recruitee and Workable links, also when a company's own careers page embeds one. Otherwise it reads the page's job data.
- **By name**: PageMint tries a few spellings on Greenhouse, Lever and Ashby. A Greenhouse match whose company name agrees is `confirmed`. A Lever or Ashby match is `guessed`, because those feeds don't include the company name, and the result says so. If it's the wrong company, remove it and pass the careers URL instead.

### Matching

Results are ranked by keywords (title matters most, then department, then description) and recency, with no AI involved. `location` is a filter that knows common alternative spellings (Bengaluru/Bangalore, Gurugram/Gurgaon, NYC/New York, UK/United Kingdom...). A remote job matches every location only when it isn't limited to a place: "Remote (worldwide)" matches London, "Remote - US" does not.

### Your data

The saved company list and a cache of job feeds live in a small SQLite file on your computer (`~/.local/share/pagemint` on Linux, `~/Library/Application Support/pagemint` on macOS, `%LOCALAPPDATA%\pagemint` on Windows; change it with `PAGEMINT_DATA_DIR`). Nothing is sent anywhere else.

### Job search limits

| Setting | Default | Environment variable |
|---|---|---|
| Job feed cache | 6 hours (free sources: 1 hour) | `PAGEMINT_JOBS_CACHE_HOURS` |
| Time budget per search | 60 s (slower feeds are listed under `skipped`) | `PAGEMINT_JOBS_SEARCH_BUDGET` |
| Saved companies | 500 | |
| Job feed size | 40 MB (large companies' feeds exceed the 5 MB page limit) | |
| Workday jobs per company | 200 | |

All job requests follow the same robots.txt, rate-limit and safety rules as everything else. Hiring-system hosts are shared by many companies (all Greenhouse boards use one host), so the first search over many saved companies can take a while; later searches use the cache.

To try job search against real sites: `python scripts/live_check.py "data engineer" --location London`.

## Web search

`search(query, limit=10, fetch_pages=0, provider="auto")` returns titles, links and snippets (up to 20). With `fetch_pages=3` it also reads the top 3 result pages and returns them as Markdown, in the same call.

There is no free web search that needs no key and allows bots, so you choose one of these. PageMint never uses a paid service unless you set its key.

### Option 1: your own SearXNG (free, no key, no monthly limit)

[SearXNG](https://docs.searxng.org/) is an open-source search engine you run yourself. With Docker:

```bash
mkdir -p searxng && printf 'use_default_settings: true\nsearch:\n  formats: [html, json]\nserver:\n  secret_key: "change-me-to-something-random"\n' > searxng/settings.yml
```

```bash
docker run -d --name searxng -p 8888:8080 -v "$PWD/searxng:/etc/searxng" searxng/searxng
```

Then set `PAGEMINT_SEARXNG_URL=http://localhost:8888`. The `formats: [html, json]` line matters: without it SearXNG refuses PageMint's requests, and PageMint tells you so. SearXNG gets its results from other search engines, so very heavy use can get it rate-limited by them.

### Option 2: your own API key

Set **one** of these environment variables with a key from your own account:

| Variable | Provider |
|---|---|
| `PAGEMINT_TAVILY_API_KEY` | [Tavily](https://tavily.com) |
| `PAGEMINT_BRAVE_API_KEY` | [Brave Search API](https://brave.com/search/api/) |

Both have offered free monthly allowances; check their current pricing pages, since these change. Keys are read only from environment variables, never from command-line options or tool arguments, and never appear in PageMint's output or logs.

### Adding the settings to Claude

Claude Code:

```bash
claude mcp add pagemint -e PAGEMINT_SEARXNG_URL=http://localhost:8888 -- uvx pagemint
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pagemint": {
      "command": "uvx",
      "args": ["pagemint"],
      "env": { "PAGEMINT_TAVILY_API_KEY": "your-key" }
    }
  }
}
```

### Which provider is used

- `provider="searxng" | "tavily" | "brave"` uses that one.
- Otherwise `PAGEMINT_SEARCH_PROVIDER` if set, else SearXNG if configured, else Tavily, else Brave.
- If it fails, PageMint does **not** switch to another provider on its own (that could move you from free SearXNG to a key that costs money). The error lists your other configured providers. Set `PAGEMINT_SEARCH_FALLBACK=1` to allow trying them automatically when no provider was pinned.

### Safety

- PageMint never scrapes Google, Bing or DuckDuckGo result pages.
- Requests that carry your key never follow redirects, so the key can't be sent to another site.
- Your SearXNG may run on `localhost`: only that exact address is allowed, and only for the search request itself. Result pages read with `fetch_pages` go through the normal checks (robots.txt, private-address block, size and time limits).
- Calls to the Tavily/Brave APIs with your key, and to your own SearXNG, are not robots.txt-checked: robots.txt is meant for crawlers, not for API calls you have a key for.
- Search results are text from other websites. Treat them as information, not instructions.

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

```bash
pagemint search "python mcp server" --fetch 2
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
| [PyYAML](https://pyyaml.org/) (development) | Checking the hosting files | MIT |

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

Publishing a GitHub release (Releases → Draft a new release, with a new tag like `v0.1.0` on `main`) builds the package, publishes it to PyPI through [trusted publishing](https://docs.pypi.org/trusted-publishers/), and then updates the [MCP Registry](https://registry.modelcontextprotocol.io) entry from `server.json` using GitHub's OIDC login, so no API token is stored anywhere. One-time setup on pypi.org: create a free account, then under **Publishing** add a pending publisher with owner `HarshaVardhan1111`, repository `pagemint`, workflow `release.yml` and environment `pypi`.

## License

MIT. See [LICENSE](LICENSE).
