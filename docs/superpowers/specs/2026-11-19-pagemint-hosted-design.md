# PageMint Hosted — Design

Date: 2026-11-19
Status: draft, awaiting approval (builder check done)
Scope: a hosted PageMint that works in claude.ai (browser) and the Claude
phone app, for anyone, at zero cost for now.

## Intent and why

PageMint today runs on the user's own computer (Claude Code / Claude
Desktop). Harsha wants it usable from claude.ai in the browser and on the
phone, for anyone, as the first step toward a later paid hosted product.
claude.ai only talks to MCP servers reachable on the public internet
("custom connectors"), so PageMint must run on a server with a public HTTPS
address. Growth will be slow at first, so Harsha will test it himself while
it grows. **No payment for now**: Oracle Cloud's Always Free tier, no
Pay-As-You-Go upgrade until real users arrive.

## Goals

- A public HTTPS MCP endpoint (Streamable HTTP) that claude.ai can add as a
  custom connector; works on the phone once added on the web.
- Sign in with GitHub (OAuth) — no passwords stored by PageMint.
- Per-user saved company lists and per-user daily limits.
- Same tools as the local version: scrape, crawl, map, jobs tools, and web
  search through a SearXNG running on the same server.
- Zero cost on Oracle Always Free; easy to rebuild if Oracle reclaims the VM.
- Deploys automatically from GitHub releases; Harsha needs only a browser.

## Non-goals

- Payments, plans, billing (later, with the paid product).
- A custom domain (later; a free `sslip.io` hostname is used now).
- Multiple servers, autoscaling, high availability.
- Any change to the local (stdio) PageMint behaviour.

## Anti-goals

- The server being usable by strangers to attack or flood other sites.
- Any user seeing another user's data (saved companies, usage).
- Secrets (OAuth client secret, signing keys, SSH keys) in the repo or logs.
- Storing page contents or search queries longer than needed.

## Approaches considered

Hosting (Harsha chose):
- Render free: no card, but sleeps when idle (slow first call), too small
  for the headless browser, no persistent disk.
- Google Cloud Run free allowance: needs a card, cold starts, needs a
  separate database.
- **Oracle Cloud Always Free VM — chosen.** Always on, large enough for the
  browser and SearXNG. Risks: card at sign-up (identity only), A1 capacity
  is often unavailable, and **idle Always Free instances can be reclaimed**
  (guides: 7 days below ~20% CPU/network/memory). Harsha declined the
  Pay-As-You-Go upgrade for now → we design for fast rebuild + backups.

Sign-in:
- No login: rejected (abuse).
- Secret URL per user: weak (URLs leak in screenshots/logs), no revocation UX.
- **OAuth 2.1 with GitHub as the identity provider — chosen.** claude.ai
  supports OAuth connectors with dynamic client registration; the MCP Python
  SDK (`mcp` 2.x) ships an authorization-server framework
  (`OAuthAuthorizationServerProvider`, `AuthSettings`, register/authorize/
  token/revoke handlers) that PageMint implements, delegating "who is this
  person" to a GitHub OAuth App.

## Architecture

```
claude.ai ──HTTPS──> Caddy (TLS, sslip.io name, Let's Encrypt)
                       └─> pagemint (serve --http, OAuth AS + MCP at /mcp)
                              ├─ SQLite (users, tokens, saved companies, usage)
                              ├─ Chromium (JS rendering, same container)
                              └─> searxng (private network only, JSON on)
```

- One Oracle A1 (ARM64) Ubuntu VM, Docker Compose with three services:
  `caddy`, `pagemint`, `searxng`. Only Caddy publishes ports (80, 443).
- Hostname: `<public-ip-with-dashes>.sslip.io` (free wildcard DNS that maps
  to the IP), so Let's Encrypt can issue a certificate without buying a
  domain. Changing to a real domain later is one setting.
- **Reserved public IP:** setup reserves a public IP and attaches it to the
  VM, so a rebuilt VM keeps the same address, hostname, OAuth callback and
  connector URL, and restored tokens keep working. (Oracle's Always Free
  limits include reserved public IPs per Oracle's resource list as recalled;
  the builder verifies. If not free, the runbook's fallback is: new
  hostname → update `PAGEMINT_PUBLIC_URL` and the GitHub OAuth App callback;
  users re-add the connector.)
- Image: built by GitHub Actions for `linux/arm64` **and** `linux/amd64` on
  each release, pushed to GitHub Container Registry (free for public repos).
- **Fallback when no A1 (ARM) capacity:** Always Free resources exist only in
  the home region, so "another region" is not an option. The fallback is the
  free 1 GB x86 VM (E2.1.Micro) with a `small` compose profile: no browser
  rendering and no SearXNG (web search off), everything else on. Switching
  to A1 later is a rebuild with the default profile.
- SearXNG's `PAGEMINT_SEARXNG_URL=http://searxng:8080` (private Docker
  network; reached via the existing scoped private-origin exception).

## New code in PageMint

1. **HTTP mode:** `pagemint serve --http [--host 0.0.0.0] [--port 8000]`
   runs the MCP server with Streamable HTTP at `/mcp`. Local stdio mode is
   unchanged and remains the default.
2. **Hosted settings** (env): `PAGEMINT_HOSTED=1`, `PAGEMINT_PUBLIC_URL`,
   `PAGEMINT_GITHUB_CLIENT_ID`, `PAGEMINT_GITHUB_CLIENT_SECRET` (secret),
   `PAGEMINT_SECRET_KEY` (secret, signs state/tokens),
   `PAGEMINT_ALLOWED_USERS` (comma list of GitHub logins; `*` = anyone),
   `PAGEMINT_MIN_GITHUB_ACCOUNT_DAYS` (default 30; newer GitHub accounts
   are refused when open to anyone, to slow down throw-away accounts),
   `PAGEMINT_DATA_DIR`.
   **Launch:** `PAGEMINT_ALLOWED_USERS=HarshaVardhan1111` while Harsha tests
   it; opening to everyone is changing it to `*` (one line) once he is happy.
3. **OAuth provider** (`src/pagemint/hosted/auth.py`): implements the SDK's
   provider interface. Dynamic client registration on (claude.ai uses it).
   `authorize` redirects to GitHub's OAuth (scope: none beyond public
   profile — `read:user`), GitHub calls back to `/oauth/github/callback`,
   PageMint maps the GitHub user id to a local user and issues its own
   authorization code → access token (opaque, random, stored **hashed**,
   1-hour expiry) + refresh token (30 days, rotated). Revocation supported.
   PageMint never stores GitHub access tokens beyond the login.
4. **Per-user context:** each tool call knows the user id from the access
   token; `JobStore` gains a `user_id` column on companies (cache stays
   shared — job feeds are public data).
5. **Limits** (`hosted/limits.py`, per user per UTC day, configurable):
   - **Pages: 200.** Every page PageMint fetches for the user counts once:
     a scrape, each crawled page, each `fetch_pages` result, each
     `check_job`, and a `map` call counts as 1. Job feeds do not count
     (they are shared, cached public data).
   - **Job searches: 100** (`search_jobs` and `company_jobs` count 1 each).
   - **Web searches: 50. Browser renders: 30.**
   - At most 2 tool calls at once per user; at most 8 tool calls at once
     server-wide; **1 browser render at a time server-wide** (others wait,
     within their time limits). Hosted crawl cap: 50 pages.
   - A crawl that runs out of the page budget stops and returns the pages
     fetched so far with a warning (same as its time budget); a single-page
     call over the limit returns error type `limit_reached` with the reset
     time.
   - **Server-wide daily cap:** 5,000 pages and 500 web searches in total;
     when reached, tools answer `limit_reached` for everyone until reset.
6. **Abuse protection (on top of the existing SSRF/robots/rate limits):**
   per-host rate limit stays global across users (1 req/s per site for the
   whole server, so many users cannot hammer one site);
   `allow_private_networks` forced off; the SearXNG exception remains the
   only private origin; a host block list file for complaints.
7. **Logs:** one line per tool call: time, user id, tool, target host,
   status, duration. No page contents, no full URLs with query strings, no
   search queries. Rotated, kept 14 days.
8. **Pages:** `/` (what PageMint is + how to add the connector in claude.ai),
   `/terms`, `/privacy` (plain text, short), `/healthz`.

## Deployment and operations

- `deploy/cloud-init.yaml`: pasted into Oracle's "create instance" form;
  installs Docker, opens ports 80/443 in the VM firewall, creates a
  `pagemint` user with the deploy SSH public key, writes the compose stack.
- `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/searxng/settings.yml`
  (JSON format on, limiter on, secret from env), `deploy/.env.example`.
- **GitHub Actions** (`deploy.yml`, on release after PyPI): build+push ARM64
  image to GHCR, then SSH to the VM and `docker compose pull && up -d`.
  Secrets (GitHub repo settings): `DEPLOY_HOST`, `DEPLOY_SSH_KEY`.
  App secrets live only in `/opt/pagemint/.env` on the VM.
- **Backups:** nightly SQLite `.backup` copy, compressed, uploaded to Oracle
  Object Storage (Always Free) through a pre-authenticated request URL kept
  in the VM's `.env`; 14 kept. `deploy/restore.md` explains restoring.
- **Rebuild runbook** (`deploy/README.md`): create VM with cloud-init → set
  `DEPLOY_HOST` → re-run deploy → restore latest backup. Target < 30 min.
- Health: `/healthz`; optional free external uptime check later.

## Harsha's one-time steps (browser only; Oracle Cloud Shell gives a
terminal in the browser)

1. Oracle Cloud sign-up (Always Free), pick a home region with A1 capacity.
2. Create the VM: Ubuntu, A1 Flex (2 OCPU / 12 GB is enough), paste
   cloud-init; add ingress rules for TCP 80/443 in the subnet's security list.
3. Create a GitHub OAuth App (callback
   `https://<host>/oauth/github/callback`); put client id/secret in the VM
   `.env` (via Cloud Shell SSH).
4. Add `DEPLOY_HOST` and `DEPLOY_SSH_KEY` as GitHub Actions secrets.
5. Publish a GitHub release → deploy runs → add the connector in claude.ai:
   Settings → Connectors → Add custom connector → `https://<host>/mcp`.

## Testing

- Unit/integration (offline): HTTP transport serves the same tools; OAuth
  flow end to end with a fake GitHub (local server) — registration,
  authorize redirect, callback, code exchange, refresh, revoke, expired and
  revoked tokens rejected, PKCE required; user isolation (user A cannot see
  B's companies); limits and reset; concurrency caps; logs contain no
  queries/contents; hosted mode refuses `allow_private_networks`.
- Container: image builds for arm64 in CI; compose stack starts in CI
  (x86 with emulation or native build) and `/healthz` answers.
- Manual: Harsha adds the connector in claude.ai and runs a job search on
  the phone.

## Risks (accepted for now)

- Oracle reclaims the idle VM → rebuild from runbook + backup (no payment
  chosen).
- A1 capacity unavailable at creation → retry/another region.
- Sites block cloud IPs more often → some scrapes fail; reported per page.
- Free Oracle allowance terms change → move later (Docker makes it portable).

## Left to the builder

Exact limit numbers within the stated defaults, Caddy/SearXNG config
details, log format, page wording, backup script details.

## Decisions made by Claude (check these)

- GitHub sign-in (OAuth) with PageMint as the OAuth server, using the SDK.
- Free `sslip.io` hostname until a domain is bought.
- SearXNG on the same server so hosted web search is free.
- Per-site rate limit shared by all users; daily per-user limits as above.
- Logs keep no page contents, URLs' query strings or search queries.
- Pure Always Free (no PAYG) → rebuild script + nightly backups instead.
- Launch allowlisted to Harsha; open to everyone later by one setting, with
  a 30-day minimum GitHub account age and server-wide daily caps.
- What counts toward limits (pages, job searches, renders) as listed;
  crawls return partial results at the limit.
- Reserved public IP so rebuilds keep the same connector URL.
- Multi-arch image and a small x86 profile as the no-ARM-capacity fallback.
