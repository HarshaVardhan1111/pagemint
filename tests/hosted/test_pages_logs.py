import json

import httpx
import pytest
from mcp import Client

from conftest import make_settings
from http_helpers import AppServer, free_port
from pagemint.client import PageMint
from pagemint.hosted.limits import Limits
from pagemint.hosted.logs import CallLogger
from pagemint.hosted.store import HostedStore
from pagemint.server import build_http_app, build_server
from test_auth import app, fake_github, sign_in, tokens_for  # noqa: F401  (fixtures and helpers)


@pytest.fixture
def hosted_app(tmp_path):
    def make(**kw):
        port = free_port()
        base = dict(hosted=True, public_url=f"http://127.0.0.1:{port}", github_client_id="cid",
                    github_client_secret="sec", secret_key="k" * 40, allowed_users="harsha", data_dir=str(tmp_path))
        base.update(kw)
        return AppServer(build_http_app(make_settings(**base)), port=port)
    return make


async def test_info_pages(hosted_app):
    with hosted_app() as srv:
        async with httpx.AsyncClient() as http:
            home = await http.get(f"{srv.url}/")
            terms = await http.get(f"{srv.url}/terms")
            privacy = await http.get(f"{srv.url}/privacy")
            health = await http.get(f"{srv.url}/healthz")
    assert home.status_code == 200 and f"{srv.url}/mcp" in home.text and "Connectors" in home.text
    assert terms.status_code == 200 and "robots.txt" in terms.text
    assert privacy.status_code == 200 and "GitHub" in privacy.text
    assert health.status_code == 200 and health.json()["ok"] is True


async def test_wrong_host_header_rejected(app):
    # Signed in, but the request names another host (DNS rebinding): refused by the transport.
    with app() as srv:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            r = await sign_in(srv.url, http)
            _, tok = await tokens_for(srv.url, http, r["callback"], r["client_id"])
            headers = {"Authorization": f"Bearer {tok.json()['access_token']}",
                       "Accept": "application/json, text/event-stream"}
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
            bad = await http.post(f"{srv.url}/mcp", headers={**headers, "Host": "evil.example"}, json=body)
            good = await http.post(f"{srv.url}/mcp", headers=headers, json=body)
    assert bad.status_code == 421
    assert good.status_code == 200


async def test_call_log_has_host_only(server, tmp_path):
    server.route("/page", "<html><body><main><p>hi</p></main></body></html>")
    log_path = tmp_path / "logs" / "calls.log"
    logger = CallLogger(str(log_path))
    limits = Limits(HostedStore(str(tmp_path / "h.db")))
    srv = build_server(client_factory=lambda: PageMint(make_settings()), limits=limits, call_logger=logger)
    async with Client(srv) as c:
        await c.call_tool("scrape", {"url": server.url("/page?q=my-secret-query")})
        await c.call_tool("search", {"query": "very private search words"})
    logger.close()
    text = log_path.read_text()
    assert "tool=scrape" in text and "host=127.0.0.1" in text and "status=ok" in text
    assert "tool=search" in text and "status=not_configured" in text
    assert "my-secret-query" not in text and "private search words" not in text and "/page" not in text
    assert "user=local" in text
