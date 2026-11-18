# PageMint Jobs — Design

Date: 2026-11-18
Status: draft, awaiting approval (builder check done)
Scope: project 2 of 3. Builds on the PageMint core (scrape/crawl/map,
robots.txt, rate limits, SSRF guard). Web search is project 3.

## Intent and why

Harsha's agents search for jobs every day. Claude's built-in options only
reach Indeed; company career pages often show jobs that are already closed;
LinkedIn cannot be used (terms, login, robots.txt). PageMint Jobs gives any
MCP user a free tool that finds **open** jobs straight from companies' own
hiring systems, with the best matches first. It must work well for everyone
who installs PageMint, not only for Harsha. Later it may become part of a
paid hosted offering, so the core stays a library that a hosted API can wrap.

## Goals

- Find open jobs at named companies, from their hiring system ("ATS") feeds.
- Remember companies the user has searched (a saved list that grows with
  use) and search across all of them at once.
- Add free cross-company job sources that allow automated access.
- Only open jobs: closed ones are filtered out, and any job link can be
  re-checked.
- Rank results so the best matches come first.
- Same safety and politeness as the core (robots.txt, per-host rate limit,
  SSRF guard, size/time limits). Fully offline tests.

## Non-goals

- LinkedIn, Indeed, Glassdoor, ZipRecruiter (terms forbid scraping or the
  sites block bots).
- Paid data providers or anything needing an API key (rule: ask Harsha first).
- Applying to jobs, accounts, logins, CAPTCHAs.
- A hosted service or shared global index (possible paid feature later).
- LLM-based matching or extraction.

## Anti-goals

- Showing closed jobs as open.
- Ignoring a source's robots.txt because the data would be useful.
- Huge tool outputs that blow up the agent's context.
- One slow or broken company feed hanging a whole search.

## What we verified (2026-11-18, from this sandbox)

| System | Public feed (keyless) | robots.txt | Notes |
|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` → `{jobs:[{id,title,location{name},absolute_url,updated_at,first_published,content(HTML-escaped),departments,offices,company_name,application_deadline}], meta:{total}}`; unknown board → 404 | allows (only `/embed/` disallowed) | all boards share one host |
| Lever | `GET api.lever.co/v0/postings/{site}?mode=json` (EU: `api.eu.lever.co`) → list of `{id,text,categories{location,commitment,team,department,allLocations},country,workplaceType,createdAt(ms),hostedUrl,applyUrl,descriptionPlain,salaryRange{min,max,currency,interval}}`; single: `/v0/postings/{site}/{id}` | `Allow: /`, `Crawl-delay: 1` | README: no full-text search, per-site only |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` → `{jobs:[{id,title,department,team,employmentType,location,secondaryLocations,isListed,isRemote,workplaceType,publishedAt,jobUrl,applyUrl,descriptionPlain,descriptionHtml,compensation{compensationTierSummary,...}}]}` | robots.txt answers 401 → treated as "no rules" (allowed) | |
| Workday | `POST {tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}` → `{total,jobPostings:[{title,externalPath,locationsText,postedOn,bulletFields}],facets}`; detail `GET .../wday/cxs/{tenant}/{site}{externalPath}` → `{jobPostingInfo:{title,jobDescription,location,startDate,timeType,jobReqId,externalUrl,canApply,...}}`; closed/missing → 404 | example tenant allows its career site; API path not disallowed | needs the careers URL (tenant, N and site cannot be guessed from a name); POST |
| SmartRecruiters | — | `User-agent: * Disallow: /` (only LinkedInBot allowed) | **not usable** |
| Workable | widget API not verifiable: Cloudflare answered 429 (error 1015) even to a single request | apply site 429; `jobs.workable.com` disallows search URLs with parameters | no dedicated reader: a Workable URL is recognised and read with the general JobPosting reader; when robots/429 blocks it, the company is skipped with that reason |
| Recruitee | `GET {company}.recruitee.com/api/offers/` (from Recruitee's docs, which allow crawling; no company host reachable here to test) | per company host, checked at run time | dedicated reader built from the documented shape; marked experimental until `live_check.py` confirms it |

Core change found during this check: the core treats any 4xx robots.txt as
"allow all". A **429** means "slow down", not "no file"; it must be treated
like 5xx (site off-limits for now). This fix is part of this project.

## Approaches considered

- **A. A dedicated reader per hiring system.** Best data (salary, remote,
  dates) and fast, but dozens of systems to write and maintain, and the long
  tail (custom portals, many Indian HR systems) is still missed.
- **B. One general reader for any careers page.** Reads the schema.org
  `JobPosting` data most job pages embed for Google's job search, including
  `validThrough` (expiry). Works almost anywhere, but slow (one job page at a
  time at 1 request/second) and misses pages without that data.
- **C. Both — chosen by Harsha.** Dedicated readers for the biggest systems,
  the general reader as fallback, a `check_job` tool to re-verify any link,
  plus Harsha's choice of how companies are found: companies named by the
  agent, remembered in a saved list, plus free cross-company sources.

## Free cross-company sources (verified 2026-11-18)

SmartRecruiters is ruled out by its robots.txt and Workable's search is
disallowed/rate-limited, so the free cross-company layer uses public job
APIs that allow automated, keyless use. Checked from this sandbox:

| Source | Endpoint | robots.txt / terms | Plan |
|---|---|---|---|
| Hacker News "Who is hiring?" | find the thread: `GET hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring` (titles "Ask HN: Who is hiring? (November 2026)"); whole thread in one request: `GET hn.algolia.com/api/v1/items/{id}` → `children[]` with `text` (HTML), `created_at` (243 top-level posts in Nov 2026) | robots.txt 404 → allowed; HN Firebase API allows `*.json` | **use** current and previous month's threads |
| Arbeitnow | `GET www.arbeitnow.com/api/job-board-api?page=N` → `{data:[{slug,company_name,title,description(HTML),remote,url,tags,job_types,location,created_at(unix)}], links{next}, meta{terms}}`, 300 per page, newest first, updated hourly | robots allows all; terms: "free public API… please do not abuse… appreciate linking back" | **use** pages 1–3 (900 newest), mostly Europe; always link back |
| Himalayas | `GET himalayas.app/jobs/api?limit=&cursor=` → `{jobs:[{title,excerpt,companyName,employmentType,minSalary,maxSalary,salaryPeriod,currency,locationRestrictions[],categories[],description,pubDate,expiryDate,applicationLink,guid}],nextCursor,totalCount(118k)}`; a search endpoint `/jobs/api/search?q=` exists but answered 429 | robots allows `/jobs/api` | **best effort**: search endpoint when a query is given, cached; on 429 skip with a warning |
| Remotive | `/api/*` | robots.txt disallows `/api/*` | **not used** |

HN posts are free text, so fields come from the community convention on
the first line ("Company | Role | Location | Remote | Salary | link"), parsed
best effort; a post whose first line has no recognisable role is kept with
`title` = first line (trimmed). HN jobs older than 45 days are dropped.
Every free-source job carries its source name and the original link.

## Tools (MCP and library)

All return JSON dicts and never raise (errors as `{"error": {...}}`, as in
the core).

### `search_jobs(query, location="", remote=None, posted_within_days=None, companies=None, sources="all", limit=25)`

- Searches the saved companies (or the `companies` list given), plus the
  free cross-company sources when `sources="all"` (`"companies"` limits it
  to company feeds).
- `remote`: `True` remote only, `False` exclude remote, `None` either.
- Returns `{"jobs": [Job...], "total_matches", "searched": {companies,
  sources}, "skipped": [{company|source, reason}], "warnings"}`.
- Ranked best match first (see Ranking). `limit` default 25, max 100.

### `company_jobs(company, query="", location="", remote=None, limit=50)`

- `company` is a company name (e.g. "Stripe") or a careers-page / job-board
  URL. PageMint finds the hiring system (see Detection), returns its open
  jobs (filtered and ranked if `query`/`location` given), and **adds the
  company to the saved list**.
- Returns `{"company": {name, system, board, careers_url}, "jobs": [...],
  "total_open", "warnings"}`. If no system is found: an error that says
  what was tried and asks for the careers-page URL.

### `check_job(url)`

- Returns `{"url", "status": "open" | "closed" | "unknown", "reason",
  "job": Job | null}` with the full description.
- Open/closed rules: ATS single-job endpoint 200 → open, 404/410 → closed;
  page 404/410 → closed; JSON-LD `validThrough` (or Himalayas
  `expiryDate`) in the past → closed; redirect to a listing page with no
  JobPosting → unknown ("probably closed"); otherwise open if JobPosting
  data is present, else unknown.
- Free-source jobs have no closed flag of their own: their link is checked
  the same way (many HN posts link to an ATS job, which gives a real
  answer); if nothing conclusive, `unknown`. In search results these jobs
  show `"status": "unverified"` and ATS-feed jobs show `"status": "open"`.

### `saved_companies(action="list", company=None)`

- `action`: `list` | `add` | `remove`. `add` runs detection like
  `company_jobs` without returning jobs. Returns the list with each
  company's system, job count and last refresh time.

### Job shape

```
{ "title", "company", "location", "locations": [..], "remote": bool|null,
  "workplace_type": "remote"|"hybrid"|"onsite"|null, "employment_type",
  "department", "posted_at": ISO date|null, "valid_through": ISO date|null,
  "salary": {"min","max","currency","period","text"}|null,
  "status": "open"|"unverified", "url", "apply_url", "source": "greenhouse"|"lever"|"ashby"|"workday"|
  "recruitee"|"workable"|"jsonld"|"<free source name>",
  "description": str }
```
In `search_jobs`/`company_jobs`, `description` is a 300-character plain-text
snippet (keeps results small); `check_job` returns the full description as
Markdown (capped at 20,000 characters like the core).

## Detection (company name or URL → hiring system)

- **URL given:** recognise known patterns directly — `boards.greenhouse.io/
  {t}`, `job-boards.greenhouse.io/{t}`, `jobs.lever.co/{s}`,
  `jobs.eu.lever.co/{s}`, `jobs.ashbyhq.com/{b}`, `{t}.wd{N}.
  myworkdayjobs.com/[{lang}/]{site}`, `{c}.recruitee.com`,
  `apply.workable.com/{a}`. Otherwise fetch the page (core scrape) and look
  for those patterns in links, iframes and scripts (many company careers
  pages embed their ATS). If none, use the general JobPosting reader.
- **Name given:** build slug candidates (lowercase; spaces removed; spaces
  as hyphens; strip suffixes like "inc", "ltd", "technologies"), max 3, and
  probe Greenhouse, Lever (global then EU) and Ashby list endpoints; a 200
  with ≥1 job is a match. Probes go through the core fetcher (robots, rate
  limit, SSRF).
- **Confirming the match:** a slug can belong to a different company with
  the same name. Greenhouse returns `company_name`; if it matches the given
  name (case/punctuation-insensitive, or one contains the other) the match
  is `confirmed`. Lever and Ashby give no company name, so their matches
  are saved as `guessed` and the result warns: "Found a Lever board
  'notion'. If this is not the company you meant, pass its careers-page
  URL." Confirmed beats guessed; among equals, most jobs wins; the others
  are listed in warnings. URL-based detection is always `confirmed`.
  Workday cannot be guessed — the error asks for the careers URL.
- Results are stored in the saved list so detection runs once per company.

## General reader (JobPosting fallback)

- Parse `<script type="application/ld+json">` blocks, including `@graph`
  and lists; accept `@type` `JobPosting` (string or list). Map title,
  hiringOrganization.name, jobLocation (address locality/region/country),
  jobLocationType `TELECOMMUTE` → remote, employmentType, datePosted,
  validThrough, baseSalary (MonetaryAmount/QuantitativeValue), url,
  description (HTML → Markdown via the core).
- For a careers page with no JobPosting itself: collect same-site links that
  look like job pages (`/job`, `/jobs/`, `/careers/`, `/position`,
  `/opening`, `/vacanc`, `/requisition`, plus links whose text matches the
  page's job-title pattern), fetch up to 30 (setting), keep those with
  JobPosting data. Uses the crawl time budget.
- Jobs with `validThrough` in the past are dropped.

## Ranking

Deterministic keyword scoring, no LLM:
- Query terms (lowercased, stop words removed, simple plural folding) —
  title match ×5, all terms in title +5 bonus, exact phrase in title +5;
  department/team ×2; description ×1 (capped).
- Location: filter, not score. Case-insensitive substring on location
  fields, with a small alias table (Bengaluru/Bangalore, Gurugram/Gurgaon,
  Mumbai/Bombay, NYC/New York, SF/San Francisco, UK/United Kingdom, US/USA/
  United States, Remote). A job marked remote matches any location only
  when `remote` is not `False`.
- Recency: +0 to +3 for jobs posted in the last 30 days; `posted_within_days`
  filters.
- Ties → newest first. Jobs with zero query score are dropped when a query
  is given.

## Storage (saved list and cache)

- SQLite via the standard library, in the user's data directory
  (`~/.local/share/pagemint` on Linux, `~/Library/Application Support/
  pagemint` on macOS, `%LOCALAPPDATA%\pagemint` on Windows), overridable
  with `PAGEMINT_DATA_DIR`. Nothing leaves the machine.
- Tables: `companies` (name, system, board, careers_url, added_at,
  last_refreshed, last_error) and `jobs` (company id or source, job JSON,
  fetched_at).
- Job feeds are cached for 6 hours (`PAGEMINT_JOBS_CACHE_HOURS`). Stale
  feeds are refreshed during a search; a feed that fails keeps its cached
  jobs with a warning showing their age.

## Performance and limits

- Company feeds refresh concurrently across hosts; the core's per-host rate
  limit still applies (all Greenhouse boards share one host, so 50
  Greenhouse companies take ~50 s the first time; later searches hit the
  cache).
- `search_jobs` has a time budget (default 60 s, setting): when it runs out,
  results use what is cached/fetched so far and `skipped` lists the rest.
- Saved list cap: 500 companies. Workday listing: up to 200 jobs per
  company per refresh (paged 20 at a time).
- Output caps as in the core; descriptions are snippets in list results.

## Core changes needed

- Fetcher: support `POST` with a JSON body (Workday), with the same checks
  as GET; accept JSON responses for API calls (`page=False` already does).
- robots.txt: treat HTTP 429 like 5xx (off-limits for now, retried after the
  short error TTL).

## Testing

All offline, against the local test server with fake feeds shaped exactly
like the verified responses above (hand-written, not copied job data):
per-system readers (list, single job, 404 → closed), detection by URL and by
name (probe order, multiple matches, nothing found), JSON-LD parsing
(@graph, lists, expired `validThrough`, salary shapes), ranking and filters,
saved list + cache (TTL, stale-on-error), time budget with partial results,
free sources (fixtures), MCP tool shapes, robots 429 rule, POST through the
safety checks. A `scripts/live_check.py` (not run in CI) lets a person try
real companies by hand.

## Left to the builder

Exact stop-word list, score weights within the ranges above, SQLite schema
details, module layout under `src/pagemint/jobs/`, slug-suffix list.

## Decisions made by Claude (check these)

- SmartRecruiters dropped (robots.txt); Workable best-effort only.
- Free cross-company layer = HN "Who is hiring?", Arbeitnow, Himalayas
  (best effort); Remotive dropped (robots.txt).
- Free-source jobs are shown as `unverified` unless their link proves them
  open; HN jobs older than 45 days dropped.
- Lever/Ashby name matches are saved as `guessed` with a warning;
  Greenhouse name matches are confirmed via `company_name`.
- Workable: no dedicated reader; general reader, skipped when blocked.
- Name detection probes Greenhouse, Lever, Ashby only; Workday needs a URL.
- 6-hour cache, 60-second search budget, 500-company saved list.
- Local SQLite storage in the user's data folder.
- Keyword ranking without an LLM; location as a filter with an alias table.
- Robots 429 → treated as off-limits (core fix).
