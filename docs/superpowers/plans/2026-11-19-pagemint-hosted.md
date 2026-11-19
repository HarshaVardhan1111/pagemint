# PageMint Hosted Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run PageMint as a public HTTPS MCP server (Streamable HTTP) with GitHub sign-in (OAuth), per-user data and limits, deployable to an Oracle Always Free VM with Docker Compose, Caddy and SearXNG.

**Architecture:** New package `src/pagemint/hosted/` (settings, SQLite user/token store, OAuth provider implementing the MCP SDK's `OAuthAuthorizationServerProvider` with GitHub as identity provider, limits, call logging, web pages). `pagemint serve --http` builds the Starlette app via `MCPServer.streamable_http_app()` and runs uvicorn. Tools read the signed-in user from `mcp.server.auth.middleware.auth_context.get_access_token().subject`. Deployment files live in `deploy/`; GitHub Actions builds a multi-arch image and deploys over SSH.

**Tech Stack:** `mcp` 2.x auth framework, Starlette (via mcp), uvicorn (via mcp, BSD), httpx, stdlib sqlite3/hashlib/secrets; Docker, Caddy, SearXNG.

**Spec:** `docs/superpowers/specs/2026-11-19-pagemint-hosted-design.md`

## Global Constraints

- No new runtime dependencies beyond what `mcp` already installs (starlette, uvicorn).
- Local stdio mode unchanged and default. `serve --http` without hosted auth may bind only to `127.0.0.1`/`localhost`.
- Hosted mode forces `allow_private_networks=False`; SearXNG at `http://searxng:8080` stays the only private origin (existing scoped exception).
- Tokens: opaque random (`secrets.token_urlsafe(32)`), stored as SHA-256 hashes; access 1 h; refresh 30 days, rotated on use; auth codes 5 min, single use; PKCE (S256) required (SDK enforces).
- GitHub scope `read:user`; GitHub tokens never stored.
- Settings: `PAGEMINT_HOSTED`, `PAGEMINT_PUBLIC_URL`, `PAGEMINT_GITHUB_CLIENT_ID`, `PAGEMINT_GITHUB_CLIENT_SECRET` (repr hidden), `PAGEMINT_SECRET_KEY` (repr hidden), `PAGEMINT_ALLOWED_USERS` (comma list; `*` = anyone), `PAGEMINT_MIN_GITHUB_ACCOUNT_DAYS` (30), `PAGEMINT_DATA_DIR`.
- Limits per user per UTC day: pages 200, job searches 100, web searches 50, renders 30; per-user concurrent calls 2; server-wide concurrent calls 8; 1 browser render at a time server-wide; hosted crawl cap 50 pages; server-wide daily pages 5,000 and web searches 500. Over limit → error type `limit_reached` with reset time; crawl returns partial results with a warning.
- Logs: time, user id, tool, target host, status, duration only; 14 daily files kept.
- Images: `linux/amd64` + `linux/arm64`; compose profiles `full` (default) and `small` (no browser, no SearXNG).

## Review Focus

1. Expired, revoked, or other-client tokens and reused auth codes → 401 / `invalid_grant`, never access. Test in Task 3.
2. GitHub login of a user not on the allowlist, or a too-new account → friendly refusal page, no token issued. Test in Task 3.
3. Two users with the same company name in their lists → each sees only their own. Test in Task 4.
4. Limits at the boundary (exactly 200th page; crawl asking 50 pages with 10 left) → 200th allowed, crawl returns 10 + warning. Test in Task 5.
5. Requests with a `Host` header other than the public host (DNS rebinding) → rejected by the transport security settings. Test in Task 6.

---

### Task 1: HTTP transport and hosted settings

**Files:** Modify `config.py`, `cli.py`, `server.py`; create `hosted/__init__.py`, `hosted/settings.py`. Test `tests/hosted/test_http.py`.

**Produces:** Settings fields above. `hosted.settings.validate_hosted(settings) -> list[str]` (missing/invalid values, e.g. PUBLIC_URL not https, empty client id/secret/secret key). `server.build_http_app(settings) -> Starlette` (no auth when not hosted). CLI `pagemint serve --http [--host H] [--port P]`; refuses non-local host when not hosted ("refusing to expose PageMint without sign-in"); in hosted mode refuses to start while `validate_hosted` returns problems (lists them).

Tests: start uvicorn on a free port in a thread (fixture), connect with `mcp.Client("http://127.0.0.1:{port}/mcp")`, list tools and call `scrape` against the local test server; `serve --http --host 0.0.0.0` without hosted → exit 2 with the message; `validate_hosted` flags http PUBLIC_URL and missing secrets; `repr(settings)` hides the two secrets.

Commit: `"Hosted step 1: Streamable HTTP mode and hosted settings"`.

### Task 2: Hosted store

**Files:** Create `hosted/store.py`. Test `tests/hosted/test_store.py`.

**Produces:** `HostedStore(path)` (SQLite, one connection + lock like `JobStore`). Tables `users(id, github_id UNIQUE, login, created_at)`, `clients(client_id, info_json)`, `auth_codes(hash, data_json, expires_at)`, `tokens(hash, kind access|refresh, data_json, expires_at, revoked)`, `pending(state, data_json, expires_at)`, `usage(user_id, day, kind, count, PRIMARY KEY(user_id, day, kind))`. Methods: `upsert_user(github_id, login) -> str user_id`, `save_client/get_client`, `put_code/take_code(code)` (single use), `put_token/get_token/revoke_token/revoke_all_for(user_id, client_id)`, `put_pending/take_pending(state)`, `add_usage(user_id, day, kind, n) -> int new_total`, `get_usage(user_id, day, kind) -> int`, `purge_expired()`. All secrets keyed by `sha256(value)`.

Tests: round trips; `take_code` twice → second None; expired rows not returned; plain token values never appear in the DB file bytes; `add_usage` atomic under 10 threads.

Commit: `"Hosted step 2: user, token and usage store"`.

### Task 3: OAuth provider with GitHub sign-in

**Files:** Create `hosted/auth.py`, `hosted/github.py`. Test `tests/hosted/test_auth.py` (fake GitHub on the local test server: `/login/oauth/authorize`, `/login/oauth/access_token`, `/user`).

**Produces:**
- `github.py`: constants `AUTHORIZE_URL`, `TOKEN_URL`, `API_URL` (overridable in tests); `async exchange_code(client, settings, code) -> str gh_token`; `async fetch_user(client, gh_token) -> dict {id, login, created_at}` (uses fetcher with headers, no redirects, robots off).
- `auth.py`: `PageMintOAuthProvider(store, settings, client: PageMint)` implementing `get_client`, `register_client`, `authorize` (stores pending {client_id, params} under random state; returns GitHub authorize URL with `client_id`, `redirect_uri={PUBLIC_URL}/oauth/github/callback`, `scope=read:user`, `state`), `load_authorization_code`, `exchange_authorization_code`, `load_refresh_token`, `exchange_refresh_token` (rotation: old refresh revoked), `load_access_token`, `revoke_token`. `auth_settings(settings) -> AuthSettings` (issuer = PUBLIC_URL, resource_server_url = PUBLIC_URL + "/mcp", client registration enabled, revocation enabled). `async github_callback(request) -> Response` (route `/oauth/github/callback`): take pending by state → exchange → fetch user → allowlist/min-age checks → upsert user → issue PageMint auth code with `subject=user_id` → 302 to the client's redirect_uri with `code` and original `state`. Refusals render a short HTML page (403) and issue nothing.

Tests: full flow via SDK HTTP endpoints with an httpx client: register → authorize (302 to fake GitHub) → callback → token exchange with PKCE → `load_access_token` gives `subject`; refresh rotates (old refresh rejected); revoked/expired access token rejected; code reuse rejected; unknown state → 400 page; not on allowlist → 403 page and no code; account younger than 30 days with `*` → 403; GitHub error → 502 page.

Commit: `"Hosted step 3: OAuth sign-in with GitHub"`.

### Task 4: Per-user data

**Files:** Modify `jobs/store.py`, `jobs/service.py`, `server.py`. Test `tests/hosted/test_users.py`.

**Produces:** `JobStore` company methods take `user_id: str = "local"` (column added with migration for existing files: `ALTER TABLE companies ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'`; unique key becomes `(user_id, key)`); job cache stays shared. `JobService(..., user_id="local")` passes it through. `server.current_user() -> str` = `get_access_token().subject` in hosted mode, `"local"` otherwise; the server builds a per-call `JobService` view for that user.

Tests: users A and B save "Acme" → each lists only their own; removing in A leaves B; old DB file without the column migrates and keeps rows as `local`; local stdio behaviour unchanged (existing tests stay green).

Commit: `"Hosted step 4: per-user saved companies"`.

### Task 5: Limits

**Files:** Create `hosted/limits.py`; modify `server.py`, `client.py` (render semaphore + render counter hook). Test `tests/hosted/test_limits.py`.

**Produces:** `Limits(store, settings)` with `async guard(user_id, tool) -> AsyncContextManager` (per-user 2 / global 8 concurrency; raises `LimitReached` immediately when exceeded), `charge(user_id, kind, n)` / `remaining(user_id, kind) -> int` against per-user and server-wide caps; `LimitReached(PageMintError)` with `type="limit_reached"` and message naming the limit and the reset time (next 00:00 UTC). Accounting per spec: scrape/check_job/map = 1 page; crawl `max_pages = min(requested, 50, remaining pages)` then charges pages returned (partial + warning when budget-limited); search `fetch_pages` capped to remaining pages and charged; `search` = 1 web search; `search_jobs`/`company_jobs` = 1 job search; renders: `PageMint(render_semaphore=asyncio.Semaphore(1), render_budget=callable)` charges 1 render per actual browser render and skips auto-fallback (with a warning) when none left.

Tests: 200th page allowed, 201st → `limit_reached`; crawl with 10 left returns ≤10 pages + warning; server-wide cap blocks a second user; third concurrent call → limit error; renders serialized (two renders never overlap, via timestamps); auto-fallback skipped when render budget is 0; usage resets on a new day (inject clock).

Commit: `"Hosted step 5: per-user and server-wide limits"`.

### Task 6: Call logs, pages and host checking

**Files:** Create `hosted/logs.py`, `hosted/pages.py`; modify `server.py`. Test `tests/hosted/test_pages_logs.py`.

**Produces:** `CallLogger(path)` (`TimedRotatingFileHandler`, daily, 14 backups) writing `ts user tool host status ms`; host = target URL's host only. Routes `/` (setup instructions with the connector URL), `/terms`, `/privacy`, `/healthz` (`{"ok": true, "version"}`). Transport security: allowed hosts = PUBLIC_URL host (+ `localhost`, `127.0.0.1` for health checks inside compose).

Tests: a scrape with `?q=secret` logs host only, no query; search logs no query text; pages return 200 with expected text; `Host: evil.example` → 421/400 from transport security.

Commit: `"Hosted step 6: call logs, info pages and host checks"`.

### Task 7: Deployment files and CI

**Files:** Create `Dockerfile`, `.dockerignore`, `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/searxng/settings.yml`, `deploy/.env.example`, `deploy/cloud-init.yaml`, `deploy/backup.sh`, `deploy/README.md` (setup + rebuild + restore runbook, Oracle reserved IP, security list 80/443, GitHub OAuth App, secrets), `.github/workflows/deploy.yml`. Test `tests/test_deploy_files.py`.

**Produces:** Dockerfile (python:3.12-slim, `pip install .[js]`, `playwright install --with-deps chromium`, non-root user, `pagemint serve --http --host 0.0.0.0 --port 8000`, HEALTHCHECK). Compose: `caddy` (ports 80/443, `PAGEMINT_HOST` env for the site address), `pagemint` (env_file `.env`, volume `/data`), `searxng` (profile `full`); `small` profile sets `PAGEMINT_JS_FALLBACK_CHARS=0` and no SearXNG. deploy.yml: on release published → buildx multi-arch → push `ghcr.io/harshavardhan1111/pagemint:{version,latest}` → if `DEPLOY_HOST` secret set: SSH, `docker compose pull && docker compose up -d`. backup.sh: sqlite3 `.backup` → gzip → `curl -T` to `PAGEMINT_BACKUP_PAR_URL`, keep 14.

Tests: YAML files parse; compose defines the three services and two profiles; cloud-init has docker install + firewall + compose write; deploy.yml has `packages: write` only where needed and skips SSH without the secret; `.env.example` contains no real secrets. Verify: if a Docker daemon can be started in the sandbox, `docker build` the image and run `/healthz`; otherwise state it is verified in CI only.

Commit: `"Hosted step 7: Docker, Compose, Caddy, SearXNG, cloud-init and deploy workflow"`.

### Task 8: Docs

README "Hosted PageMint" section (what it is, how to add the connector in claude.ai and on the phone, limits, privacy), link to `deploy/README.md`. Full suite green.

Commit: `"Hosted step 8: hosted docs"`.
