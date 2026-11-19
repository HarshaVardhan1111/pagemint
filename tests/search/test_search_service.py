import json
import logging

import pytest

from conftest import Response, make_settings
from pagemint.client import PageMint
from pagemint.search import brave, service, tavily
from pagemint.search.service import run_search

JSON = {"Content-Type": "application/json"}
TKEY, BKEY = "tvly-SECRET-AAAA", "BSA-SECRET-BBBB"


def js(o):
    return json.dumps(o)


def results(prefix, server, n=3):
    return [{"title": f"{prefix} {i}", "url": server.url(f"/page{i}"), "content": f"{prefix} snippet {i}",
             "description": f"{prefix} snippet {i}"} for i in range(n)]


@pytest.fixture
def routes(server, monkeypatch):
    root = server.url("").rstrip("/")
    monkeypatch.setattr(tavily, "BASE", root + "/tav")
    monkeypatch.setattr(brave, "BASE", root + "/brv")
    server.route("/sx/search", Response(js({"results": results("SX", server)}), headers=JSON))
    server.route("/tav/search", Response(js({"results": results("TV", server)}), headers=JSON))
    server.route("/brv/res/v1/web/search", Response(js({"web": {"results": results("BR", server)}}), headers=JSON))
    for i in range(3):
        server.route(f"/page{i}", f"<html><head><title>Page {i}</title></head><body><main><p>Body of page {i}</p></main></body></html>")
    return server


async def run(settings, **kw):
    async with PageMint(settings) as c:
        return await run_search(c, **kw)


async def test_not_configured():
    r = await run(make_settings(), query="x")
    assert r["error"]["type"] == "not_configured"
    msg = r["error"]["message"]
    assert "PAGEMINT_SEARXNG_URL" in msg and "PAGEMINT_TAVILY_API_KEY" in msg and "formats" in msg


async def test_auto_prefers_searxng_then_tavily_then_brave(routes):
    all3 = dict(searxng_url=routes.url("/sx"), tavily_api_key=TKEY, brave_api_key=BKEY)
    assert (await run(make_settings(**all3), query="x"))["provider"] == "searxng"
    assert (await run(make_settings(tavily_api_key=TKEY, brave_api_key=BKEY), query="x"))["provider"] == "tavily"
    r = await run(make_settings(brave_api_key=BKEY), query="x", limit=2)
    assert r["provider"] == "brave" and [x["title"] for x in r["results"]] == ["BR 0", "BR 1"]


async def test_pinned_by_argument_and_env(routes):
    s = make_settings(searxng_url=routes.url("/sx"), tavily_api_key=TKEY)
    assert (await run(s, query="x", provider="tavily"))["provider"] == "tavily"
    s2 = make_settings(searxng_url=routes.url("/sx"), tavily_api_key=TKEY, search_provider="tavily")
    assert (await run(s2, query="x"))["provider"] == "tavily"


async def test_unconfigured_provider_requested(routes):
    r = await run(make_settings(tavily_api_key=TKEY), query="x", provider="brave")
    assert r["error"]["type"] == "invalid_argument" and "tavily" in r["error"]["message"]
    assert (await run(make_settings(tavily_api_key=TKEY), query="x", provider="google"))["error"]["type"] == "invalid_argument"


async def test_no_fallback_by_default(routes):
    routes.route("/sx/search", Response("down", status=503, headers={"Content-Type": "text/plain"}))
    r = await run(make_settings(searxng_url=routes.url("/sx"), tavily_api_key=TKEY), query="x")
    assert "error" in r and "tavily" in r["error"]["message"] and "PAGEMINT_SEARCH_FALLBACK" in r["error"]["message"]
    assert routes.hits("/tav/search") == 0


async def test_fallback_when_enabled(routes):
    routes.route("/sx/search", Response("down", status=503, headers={"Content-Type": "text/plain"}))
    s = make_settings(searxng_url=routes.url("/sx"), tavily_api_key=TKEY, search_fallback=True)
    r = await run(s, query="x")
    assert r["provider"] == "tavily" and any("searxng" in w.lower() and "tavily" in w for w in r["warnings"])


async def test_unreachable_searxng_falls_back_when_enabled(routes):
    s = make_settings(searxng_url="http://127.0.0.1:1", tavily_api_key=TKEY, search_fallback=True)
    assert (await run(s, query="x"))["provider"] == "tavily"


async def test_no_fallback_when_pinned(routes):
    routes.route("/sx/search", Response("down", status=503, headers={"Content-Type": "text/plain"}))
    s = make_settings(searxng_url=routes.url("/sx"), tavily_api_key=TKEY, search_fallback=True)
    r = await run(s, query="x", provider="searxng")
    assert "error" in r and routes.hits("/tav/search") == 0


async def test_fetch_pages(routes):
    r = await run(make_settings(tavily_api_key=TKEY), query="x", fetch_pages=2)
    assert [p["title"] for p in r["pages"]] == ["Page 0", "Page 1"]
    assert "Body of page 0" in r["pages"][0]["markdown"]


async def test_fetch_pages_errors_do_not_drop_results(routes):
    routes.route("/robots.txt", Response("User-agent: *\nDisallow: /page1\n", headers={"Content-Type": "text/plain"}))
    s = make_settings(tavily_api_key=TKEY, allow_private_networks=True)
    r = await run(s, query="x", fetch_pages=2)
    assert len(r["results"]) == 3
    assert "markdown" in r["pages"][0] and r["pages"][1]["error"]["type"] == "robots"


async def test_fetch_pages_private_result_blocked(routes):
    # Strict client + SearXNG on localhost: search allowed, but result pages on a private address are not.
    s = make_settings(searxng_url=routes.url("/sx"), allow_private_networks=False)
    r = await run(s, query="x", fetch_pages=1)
    assert len(r["results"]) == 3 and r["pages"][0]["error"]["type"] == "blocked"


async def test_fetch_pages_budget(routes, monkeypatch):
    monkeypatch.setattr(service, "PAGES_BUDGET", 0.5)
    routes.route("/page0", Response("<p>slow</p>", delay=2))
    r = await run(make_settings(tavily_api_key=TKEY), query="x", fetch_pages=1)
    assert r["pages"][0]["error"]["type"] == "timeout"


async def test_limits_clamped(routes):
    r = await run(make_settings(tavily_api_key=TKEY), query="x", limit=500, fetch_pages=50)
    assert len(r["results"]) <= 20 and len(r["pages"]) <= 5
    assert (await run(make_settings(tavily_api_key=TKEY), query="  "))["error"]["type"] == "invalid_argument"


async def test_keys_never_in_output_or_logs(routes, caplog):
    caplog.set_level(logging.DEBUG)
    routes.route("/tav/search", Response(js({"detail": f"bad key {TKEY}"}), status=401, headers=JSON))
    s = make_settings(tavily_api_key=TKEY, brave_api_key=BKEY, search_fallback=True)
    r = await run(s, query="x")
    blob = json.dumps(r) + caplog.text
    assert TKEY not in blob and BKEY not in blob
    assert r["provider"] == "brave"
