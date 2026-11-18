# PageMint Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add job search to PageMint: open jobs from company hiring systems (Greenhouse, Lever, Ashby, Workday, Recruitee), any careers page via JobPosting data, and free sources (HN Who is hiring, Arbeitnow, Himalayas), with a saved company list, ranking and an open/closed check.

**Architecture:** New package `src/pagemint/jobs/`. Readers turn each source into a common `Job`. A `JobService` (owning a SQLite `JobStore`) implements `search_jobs`, `company_jobs`, `check_job`, `saved_companies` on top of the existing `PageMint` client, whose fetcher keeps robots/rate-limit/SSRF on every request. MCP tools are thin wrappers.

**Tech Stack:** Python 3.11+, existing deps only (httpx, lxml, markdownify), stdlib `sqlite3`, `json`, `html`.

**Spec:** `docs/superpowers/specs/2026-11-18-pagemint-jobs-design.md`

## Global Constraints

- No new runtime dependencies; no API keys; no paid services.
- Every network call goes through `PageMint.fetcher` (robots.txt, per-host rate limit, SSRF, size/time limits).
- Tools never raise; errors as `{"error": {"type", "message"}}`.
- Tests offline only (local test server, fixtures shaped like the verified responses; hand-written data).
- Defaults: cache 6 h (`PAGEMINT_JOBS_CACHE_HOURS`), search budget 60 s, saved list cap 500, `limit` default 25 max 100 (search) / 50 (company_jobs), snippet 300 chars, full description cap 20,000 chars, HN max age 45 days, Workday max 200 jobs/company (pages of 20), careers-page job crawl max 30 pages, Arbeitnow pages 1–3.
- Data dir: `PAGEMINT_DATA_DIR`, else `~/.local/share/pagemint` (Linux), `~/Library/Application Support/pagemint` (macOS), `%LOCALAPPDATA%\pagemint` (Windows).

## Review Focus

1. Greenhouse `content` is HTML-escaped HTML; Lever/Ashby give HTML+plain → snippets must be clean plain text without tags or entities. Test in Task 3.
2. Jobs missing date, location or salary → no crash; sorted after dated jobs on ties. Test in Task 2.
3. A company feed with hundreds of jobs (Ashby returned 834) → results respect `limit` and snippet size. Test in Task 7.
4. Two searches running at once writing the same SQLite store → no "database is locked" crash. Test in Task 6.
5. Data directory not writable → tools still work with an in-memory store and a warning. Test in Task 6.

---

### Task 1: Core changes (robots 429, POST/JSON fetch)

**Files:** Modify `src/pagemint/robots.py`, `src/pagemint/fetcher.py`. Test: `tests/test_robots.py`, `tests/test_safety.py`; extend `tests/conftest.py` with `do_POST` (same route table; `RequestInfo.body: bytes`, `method: str`).

**Produces:**
- `Fetcher.fetch(url, *, page=True, check_robots=True, method="GET", json_body: dict | None = None) -> FetchResult` (POST sends JSON with `Content-Type: application/json`; redirects on POST are not followed → `FetchError`).
- `Fetcher.fetch_json(url, *, method="GET", json_body=None) -> tuple[int, Any]` → `(status_code, parsed_json_or_None)`; non-JSON body on 2xx → `FetchError("... did not return JSON")`.

Tests:
- `test_robots_429_disallows` → scrape error type `robots`, message contains `429`.
- `test_post_json_goes_through_checks`: server records method `POST`, body JSON equals sent dict, UA header set; with robots `Disallow: /api` → `robots` error and 0 hits.
- `test_fetch_json_parses_and_rejects_html`.

Commit: `"Jobs step 1: robots 429 rule and JSON/POST fetching"`.

### Task 2: Job model and ranking

**Files:** Create `src/pagemint/jobs/__init__.py`, `jobs/model.py`, `jobs/rank.py`. Test `tests/jobs/test_rank.py` (add `tests/jobs/__init__.py`-free layout: tests import `conftest` from `tests/`).

**Produces:**
- `@dataclass Job(title:str, company:str, url:str, source:str, location:str="", locations:list[str]=[], remote:bool|None=None, workplace_type:str|None=None, employment_type:str|None=None, department:str|None=None, posted_at:str|None=None, valid_through:str|None=None, salary:dict|None=None, apply_url:str|None=None, description:str="", status:str="open", job_id:str|None=None)`; `Job.to_dict(snippet: bool=True) -> dict` (snippet → `description` = plain text ≤300 chars; else Markdown ≤20,000).
- `model.html_to_text(html: str) -> str` (unescape, strip tags, collapse spaces), `model.parse_date(value) -> str|None` (ISO date `YYYY-MM-DD` from ISO strings, unix seconds or ms).
- `rank.filter_and_rank(jobs, query="", location="", remote=None, posted_within_days=None, today: date|None=None) -> list[Job]` with the spec's weights (title ×5, all terms in title +5, phrase in title +5, department ×2, description ×1 capped at 5, recency +0..3 over 30 days; zero query score dropped when query given; ties newest first, undated last).
- `rank.LOCATION_ALIASES` per spec (Bengaluru/Bangalore, Gurugram/Gurgaon, Mumbai/Bombay, NYC/New York, SF/San Francisco, UK/United Kingdom, US/USA/United States).

Tests: title beats description match; phrase bonus; stop words ignored ("engineer in python" == "python engineer"); `location="Bangalore"` matches "Bengaluru, India"; remote job matches any location unless `remote=False`; `remote=True` keeps only remote; `posted_within_days=7` drops older and undated; undated jobs sort last (Review Focus 2); `html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;")` handled for double-escaped input → `"Hi & bye"`; `parse_date(1786469891368) == "2026-08-..."`, `parse_date("2026-11-05T02:20:02.702+00:00") == "2026-11-05"`.

Commit: `"Jobs step 2: job model and ranking"`.

### Task 3: Hiring-system readers

**Files:** Create `jobs/readers/__init__.py` (registry), `greenhouse.py`, `lever.py`, `ashby.py`, `workday.py`, `recruitee.py`, `jobs/detect.py` (URL patterns only here). Tests `tests/jobs/test_readers.py`, `tests/jobs/test_detect_url.py`, fixtures in `tests/jobs/fixtures.py` (dicts shaped like the spec's verified responses).

**Produces:**
- `@dataclass Board(system: str, board: str, host: str|None=None, site: str|None=None, careers_url: str|None=None)` in `jobs/model.py` (Workday: board=tenant, host=`{tenant}.wd{N}.myworkdayjobs.com`, site; Lever: host `api.lever.co` or `api.eu.lever.co`).
- Each reader module: `async def list_jobs(client: PageMint, board: Board) -> tuple[list[Job], str|None company_name]` and `async def get_job(client, board, job_id: str) -> Job|None` (None ⇔ 404/410). Endpoint bases are module constants so tests can point them at the local server (`BASE = "https://boards-api.greenhouse.io"` etc.; tests monkeypatch to `server.url("")`).
- `readers.READERS: dict[str, module]` for `greenhouse|lever|ashby|workday|recruitee`.
- `detect.board_from_url(url: str) -> Board|None` for the spec's URL patterns (a Workable URL returns `Board("workable", account, careers_url=url)`, read later by the general reader), plus `detect.job_ref_from_url(url) -> tuple[Board, str job_id]|None` for single-job URLs (`boards.greenhouse.io/{t}/jobs/{id}`, `job-boards.greenhouse.io/{t}/jobs/{id}`, `jobs.lever.co/{s}/{id}`, `jobs.ashbyhq.com/{b}/{id}`, `{t}.wd{N}.myworkdayjobs.com/[{lang}/]{site}/job/...`).

Tests per reader: list maps fields (title, location, remote/workplace, posted_at, salary when present, url, apply_url, snippet clean — Review Focus 1); unknown board → `FetchError` with "not found"; `get_job` 404 → `None`. Workday: POST body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`, pages until 200 jobs or `total` reached, `postedOn` "Posted 3 Days Ago" → date. URL detection table test for every pattern including `jobs.eu.lever.co` → `api.eu.lever.co`, Workday with and without `/en-US/`.

Commit: `"Jobs step 3: Greenhouse, Lever, Ashby, Workday and Recruitee readers"`.

### Task 4: General JobPosting reader

**Files:** Create `jobs/jsonld.py`. Test `tests/jobs/test_jsonld.py`.

**Produces:**
- `parse_jobpostings(html: str, page_url: str) -> list[Job]` (source `"jsonld"`; handles `@graph`, lists, `@type` list, baseSalary MonetaryAmount/QuantitativeValue, `jobLocationType: TELECOMMUTE`, `hiringOrganization` string or object; drops expired `validThrough`; ignores malformed JSON blocks).
- `async read_careers_page(client: PageMint, url: str, deadline: float) -> list[Job]` (JobPosting on the page itself; else follow up to 30 same-site job-looking links per spec patterns, stop at deadline).

Tests: graph/list/type-list, salary shapes, expired dropped, malformed block ignored, careers page with 3 job links (one without JobPosting) → 2 jobs; deadline stops early.

Commit: `"Jobs step 4: general JobPosting reader"`.

### Task 5: Company detection

**Files:** Modify `jobs/detect.py`. Test `tests/jobs/test_detect.py`.

**Produces:** `@dataclass Detection(board: Board|None, company_name: str, confidence: "confirmed"|"guessed"|None, warnings: list[str])`; `async detect_company(client: PageMint, company: str) -> Detection`.
- URL → `board_from_url`; else fetch page and scan links/iframes/scripts for ATS URLs; else `Board("jsonld", url, careers_url=url)`; confidence `confirmed`.
- Name → `slug_candidates(name) -> list[str]` (max 3; strip inc/ltd/llc/gmbh/technologies/labs/corp), probe Greenhouse, Lever (global then EU), Ashby; Greenhouse `company_name` match → `confirmed`; Lever/Ashby → `guessed` + warning text from the spec; confirmed beats guessed, then most jobs.
- Nothing found → `board=None`, warning asking for the careers-page URL (mentions Workday).

Tests: slug candidates for "Acme Technologies Inc."; Greenhouse confirmed; Lever guessed warning text contains "If this is not the company you meant"; confirmed beats a larger guessed match; careers page embedding `https://boards.greenhouse.io/acme` detected; nothing found message.

Commit: `"Jobs step 5: company detection by name or URL"`.

### Task 6: Saved list and cache

**Files:** Create `jobs/store.py`. Test `tests/jobs/test_store.py`.

**Produces:** `JobStore(path: str|None)` (None or failure → in-memory with `self.warning`); `default_data_dir() -> Path`; methods `save_company(name, board: Board, confidence) -> int`, `remove_company(name_or_id) -> bool`, `list_companies() -> list[dict]`, `get_company(name) -> dict|None`, `cache_jobs(key: str, jobs: list[Job])`, `cached_jobs(key, max_age_s) -> tuple[list[Job], age_s]|None` (returns stale data when `max_age_s=None`), `set_error(key, msg)`. Connection per call with `timeout=10`, WAL mode; cap 500 companies (`save_company` raises `InvalidArgumentError` beyond).

Tests: round trip; TTL expiry; stale read; concurrent writes from 10 threads → no error (Review Focus 4); unwritable dir (`chmod 0o500`, skip on Windows/root) → in-memory + warning (Review Focus 5); cap 500.

Commit: `"Jobs step 6: saved company list and job cache"`.

### Task 7: Free sources

**Files:** Create `jobs/sources/__init__.py` (`SOURCES` registry), `hn.py`, `arbeitnow.py`, `himalayas.py`. Test `tests/jobs/test_sources.py`.

**Produces:** each `async def fetch(client: PageMint, query: str) -> list[Job]` with module `BASE` constants; jobs have `status="unverified"`, source name `"hn"|"arbeitnow"|"himalayas"`.
- HN: find current and previous month "Ask HN: Who is hiring?" via search_by_date, then `items/{id}`; parse first line by `|`; title = role-looking field (contains engineer/developer/manager/designer/scientist/analyst/lead/intern or similar word list) else first line ≤120 chars; location/remote from fields; first link in text = `url` (else HN item URL); drop posts > 45 days.
- Arbeitnow: pages 1–3, stop early when `links.next` is null.
- Himalayas: query → `/jobs/api/search?q=`; no query → `/jobs/api?limit=20`; 429 → raise `FetchError` (caller turns into skipped+warning); `expiryDate` past → dropped; salary from min/max/currency/period.

Tests with fixtures for each; HN parsing table (pipe line with REMOTE, line without pipes, entities `&#x2F;`); Ashby-size feed through `filter_and_rank` + `limit` (Review Focus 3, here via 834 synthetic jobs and `to_dict` snippet ≤300).

Commit: `"Jobs step 7: Hacker News, Arbeitnow and Himalayas sources"`.

### Task 8: JobService and MCP tools

**Files:** Create `jobs/service.py`; modify `src/pagemint/server.py`, `src/pagemint/config.py` (settings `jobs_cache_hours=6.0`, `jobs_search_budget=60.0`, `data_dir=""`). Test `tests/jobs/test_service.py`, extend `tests/test_server.py`.

**Produces:** `JobService(client: PageMint, store: JobStore)` with async `search_jobs(...)`, `company_jobs(...)`, `check_job(url)`, `saved_companies(action="list", company=None)` returning the spec's shapes; concurrency across companies via `asyncio.gather` under the search budget (results so far + `skipped`); cached feeds used within TTL, stale-on-error with age warning; source errors → `skipped` + warning; `check_job` rules from the spec (ATS ref → reader `get_job`; else fetch page: 404/410 closed, JobPosting valid → open, expired → closed, else unknown). MCP tools `search_jobs`, `company_jobs`, `check_job`, `saved_companies` (server creates one `JobService` lazily, closed in lifespan).

Tests: company_jobs saves company and returns ranked jobs; search across two saved companies + one source, ranked; budget hit → partial + skipped; stale cache used when refresh fails; check_job open/closed/unknown for ATS URL, 404 page, expired JSON-LD, plain page; saved_companies add/list/remove; MCP lists 7 tools and `search_jobs` returns JSON with `jobs`.

Commit: `"Jobs step 8: job search service and MCP tools"`.

### Task 9: Docs and live check

README section "Job search" (tools, sources table incl. "considered, not used": SmartRecruiters, Remotive, Workable search, LinkedIn/Indeed/Glassdoor/ZipRecruiter with reasons; credits to Arbeitnow/Himalayas/HN; data stays local; limits), `scripts/live_check.py` (not in tests; runs `company_jobs` for a few public boards and `search_jobs("python")`, prints counts). Verify: full suite green; `python scripts/live_check.py` run here against allowed hosts and output read.

Commit: `"Jobs step 9: job search docs and live check script"`.
