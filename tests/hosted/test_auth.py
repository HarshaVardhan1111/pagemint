import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import Response, make_settings
from http_helpers import AppServer, free_port
from pagemint.hosted import github
from pagemint.server import build_http_app

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
JSON = {"Content-Type": "application/json"}


def gh_user(login="harsha", days_old=400, uid=42):
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_old * 86400))
    return {"id": uid, "login": login, "created_at": created}


@pytest.fixture
def fake_github(server, monkeypatch):
    root = server.url("").rstrip("/")
    monkeypatch.setattr(github, "AUTHORIZE_URL", root + "/login/oauth/authorize")
    monkeypatch.setattr(github, "TOKEN_URL", root + "/login/oauth/access_token")
    monkeypatch.setattr(github, "API_URL", root + "/api")
    server.route("/login/oauth/access_token", Response(json.dumps({"access_token": "gh-token"}), headers=JSON))
    server.route("/api/user", Response(json.dumps(gh_user()), headers=JSON))
    return server


@pytest.fixture
def app(fake_github, tmp_path):
    def make(**overrides):
        port = free_port()
        base = dict(hosted=True, public_url=f"http://127.0.0.1:{port}", github_client_id="gh-client",
                    github_client_secret="gh-secret", secret_key="k" * 40, allowed_users="harsha",
                    data_dir=str(tmp_path))
        base.update(overrides)
        return AppServer(build_http_app(make_settings(**base)), port=port)
    return make


async def sign_in(base: str, http: httpx.AsyncClient, state="abc", auth_method="none") -> dict:
    reg = await http.post(f"{base}/register", json={
        "redirect_uris": [REDIRECT], "token_endpoint_auth_method": auth_method,
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "client_name": "Claude"})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    auth = await http.get(f"{base}/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": state,
        "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": "pagemint"})
    assert auth.status_code == 302, auth.text
    gh = urlsplit(auth.headers["location"])
    gh_q = parse_qs(gh.query)
    assert gh.path.endswith("/login/oauth/authorize")
    assert gh_q["client_id"] == ["gh-client"] and gh_q["scope"] == ["read:user"]
    assert gh_q["redirect_uri"] == [f"{base}/oauth/github/callback"]
    cb = await http.get(f"{base}/oauth/github/callback", params={"code": "gh-code", "state": gh_q["state"][0]})
    return {"client_id": client_id, "client_secret": reg.json().get("client_secret"), "callback": cb}


async def tokens_for(base, http, cb, client_id, client_secret=None):
    q = parse_qs(urlsplit(cb.headers["location"]).query)
    data = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": VERIFIER}
    if client_secret:
        data["client_secret"] = client_secret
    tok = await http.post(f"{base}/token", data=data)
    return q, tok


async def test_full_sign_in_flow(app):
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http)
            cb = r["callback"]
            assert cb.status_code == 302 and cb.headers["location"].startswith(REDIRECT)
            q, tok = await tokens_for(srv.url, http, cb, r["client_id"])
            assert q["state"] == ["abc"]
            assert tok.status_code == 200, tok.text
            body = tok.json()
            assert body["access_token"] and body["refresh_token"] and body["expires_in"] == 3600
            # the MCP endpoint needs the token
            assert (await http.post(f"{srv.url}/mcp", json={})).status_code == 401
            ok = await http.post(f"{srv.url}/mcp", headers={
                "Authorization": f"Bearer {body['access_token']}", "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
            assert ok.status_code == 200, ok.text
            # the code works once only
            again = await http.post(f"{srv.url}/token", data={
                "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
                "client_id": r["client_id"], "code_verifier": VERIFIER})
            assert again.status_code == 400
            # refresh rotates: new pair works, old refresh token no longer does
            ref = await http.post(f"{srv.url}/token", data={
                "grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": r["client_id"]})
            assert ref.status_code == 200, ref.text
            old = await http.post(f"{srv.url}/token", data={
                "grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": r["client_id"]})
            assert old.status_code == 400


async def test_revoked_token_is_refused(app):
    # The SDK's revoke endpoint authenticates the client, so this uses a client with a secret.
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http, auth_method="client_secret_post")
            _, tok = await tokens_for(srv.url, http, r["callback"], r["client_id"], r["client_secret"])
            access = tok.json()["access_token"]
            rv = await http.post(f"{srv.url}/revoke", data={
                "token": access, "client_id": r["client_id"], "client_secret": r["client_secret"]})
            assert rv.status_code == 200, rv.text
            denied = await http.post(f"{srv.url}/mcp", headers={"Authorization": f"Bearer {access}"}, json={})
            assert denied.status_code == 401


async def test_unknown_state_is_refused(app):
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await http.get(f"{srv.url}/oauth/github/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 400 and "expired" in r.text.lower()


async def test_not_on_allowlist(app, fake_github):
    fake_github.route("/api/user", Response(json.dumps(gh_user(login="stranger")), headers=JSON))
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            cb = (await sign_in(srv.url, http))["callback"]
    assert cb.status_code == 403 and "invite" in cb.text.lower()


async def test_new_account_refused_when_open_to_everyone(app, fake_github):
    fake_github.route("/api/user", Response(json.dumps(gh_user(login="newbie", days_old=3)), headers=JSON))
    with app(allowed_users="*") as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            cb = (await sign_in(srv.url, http))["callback"]
    assert cb.status_code == 403 and "30 days" in cb.text


async def test_open_to_everyone_accepts_older_account(app, fake_github):
    fake_github.route("/api/user", Response(json.dumps(gh_user(login="someone", days_old=100)), headers=JSON))
    with app(allowed_users="*") as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            cb = (await sign_in(srv.url, http))["callback"]
    assert cb.status_code == 302


async def test_github_failure_page(app, fake_github):
    fake_github.route("/login/oauth/access_token", Response(json.dumps({"error": "bad_verification_code"}), headers=JSON))
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            cb = (await sign_in(srv.url, http))["callback"]
    assert cb.status_code == 502 and "gh-secret" not in cb.text


async def test_github_cancel(app):
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http)
            # a second authorize gives a fresh state; GitHub says the user cancelled
            auth = await http.get(f"{srv.url}/authorize", params={
                "response_type": "code", "client_id": r["client_id"], "redirect_uri": REDIRECT, "state": "s2",
                "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": "pagemint"})
            st = parse_qs(urlsplit(auth.headers["location"]).query)["state"][0]
            cb = await http.get(f"{srv.url}/oauth/github/callback", params={"error": "access_denied", "state": st})
    assert cb.status_code == 403 and "cancel" in cb.text.lower()


async def mcp_call(http, base, token, tool, args):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    init = await http.post(f"{base}/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    sid = init.headers.get("mcp-session-id")
    if sid:
        headers["mcp-session-id"] = sid
    await http.post(f"{base}/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r = await http.post(f"{base}/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    text = r.text
    if "data:" in text:  # event-stream answer
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    return json.loads(json.loads(text)["result"]["content"][0]["text"])


async def test_each_user_sees_only_their_saved_companies(app, fake_github, monkeypatch):
    from fixtures_jobs import GREENHOUSE_FEED
    from pagemint.jobs.readers import greenhouse

    monkeypatch.setattr(greenhouse, "BASE", fake_github.url("").rstrip("/"))
    fake_github.route("/v1/boards/acme/jobs", Response(json.dumps(GREENHOUSE_FEED), headers=JSON))
    with app(allowed_users="alice,bob") as srv:
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as http:
            tokens = {}
            for i, login in enumerate(("alice", "bob")):
                fake_github.route("/api/user", Response(json.dumps(gh_user(login=login, uid=100 + i)), headers=JSON))
                r = await sign_in(srv.url, http, state=login)
                _, tok = await tokens_for(srv.url, http, r["callback"], r["client_id"])
                tokens[login] = tok.json()["access_token"]
            added = await mcp_call(http, srv.url, tokens["alice"], "saved_companies",
                                   {"action": "add", "company": "https://boards.greenhouse.io/acme"})
            assert added["count"] == 1, added
            assert (await mcp_call(http, srv.url, tokens["bob"], "saved_companies", {}))["count"] == 0
            assert (await mcp_call(http, srv.url, tokens["alice"], "saved_companies", {}))["count"] == 1


# ---- final review fixes ----

async def test_only_known_redirect_uris_can_register(app):
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            def reg(uris):
                return http.post(f"{srv.url}/register", json={
                    "redirect_uris": uris, "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
            evil = await reg(["https://evil.example/cb"])
            mixed = await reg([REDIRECT, "https://evil.example/cb"])
            claude = await reg([REDIRECT])
            local = await reg(["http://localhost:33418/callback", "http://127.0.0.1:5000/cb"])
    assert evil.status_code == 400 and "redirect" in evil.text
    assert mixed.status_code == 400
    assert claude.status_code == 201 and local.status_code == 201


async def test_removed_from_allowlist_loses_access(app, tmp_path):
    with app(allowed_users="harsha") as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http)
            _, tok = await tokens_for(srv.url, http, r["callback"], r["client_id"])
            body = tok.json()
    # same data folder, harsha no longer on the list
    with app(allowed_users="someone-else") as srv2:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            denied = await http.post(f"{srv2.url}/mcp", headers={"Authorization": f"Bearer {body['access_token']}"}, json={})
            ref = await http.post(f"{srv2.url}/token", data={
                "grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": r["client_id"]})
    assert denied.status_code == 401
    assert ref.status_code == 400


async def test_blocked_user_loses_access(app, tmp_path):
    from pagemint.cli import main as cli_main

    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http)
            _, tok = await tokens_for(srv.url, http, r["callback"], r["client_id"])
            access = tok.json()["access_token"]
            assert cli_main(["admin", "block", "harsha", "--data-dir", str(tmp_path)]) == 0
            denied = await http.post(f"{srv.url}/mcp", headers={"Authorization": f"Bearer {access}"}, json={})
            again = await sign_in(srv.url, http, state="again")
    assert denied.status_code == 401
    assert again["callback"].status_code == 403 and "blocked" in again["callback"].text.lower()


async def test_pending_logins_are_capped_and_purged(app, monkeypatch):
    import pagemint.hosted.auth as auth_mod

    monkeypatch.setattr(auth_mod, "MAX_PENDING", 3)
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            reg = await http.post(f"{srv.url}/register", json={
                "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
            cid = reg.json()["client_id"]
            codes = []
            for i in range(5):
                a = await http.get(f"{srv.url}/authorize", params={
                    "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "state": f"s{i}",
                    "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": "pagemint"})
                codes.append((a.status_code, a.headers.get("location", "")))
    assert all("github" in loc or "authorize" in loc for _, loc in codes[:3])
    assert "temporarily_unavailable" in codes[4][1] or codes[4][0] in (429, 503)


def test_purge_runs_from_provider(tmp_path):
    import time as _t

    from pagemint.hosted.auth import PageMintOAuthProvider
    from pagemint.hosted.store import HostedStore

    store = HostedStore(str(tmp_path / "h.db"))
    store.put_pending("old", {}, expires_at=_t.time() - 5)
    p = PageMintOAuthProvider(store, make_settings(), None)
    p._last_purge = 0
    p.maybe_purge()
    assert store.take_pending("old") is None and store.count("pending") == 0
