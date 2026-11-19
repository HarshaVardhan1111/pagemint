import json
import logging

import pytest

from conftest import Response, make_settings
from pagemint.client import PageMint
from pagemint.errors import PageMintError
from pagemint.search import brave, searxng, tavily
from pagemint.search.model import SearchResult, clean_results

JSON = {"Content-Type": "application/json"}
TAVILY_KEY = "tvly-SECRETKEY-1"
BRAVE_KEY = "BSA-SECRETKEY-2"


def js(o):
    return json.dumps(o)


@pytest.fixture
async def keyed(server, monkeypatch):
    root = server.url("").rstrip("/")
    monkeypatch.setattr(tavily, "BASE", root)
    monkeypatch.setattr(brave, "BASE", root)
    c = PageMint(make_settings(tavily_api_key=TAVILY_KEY, brave_api_key=BRAVE_KEY))
    yield c
    await c.aclose()


def test_clean_results_drops_bad_rows():
    raw = [
        SearchResult("<b>Good</b> one", "https://a.example/x#frag", "snippet &amp; more"),
        SearchResult("Dup", "https://a.example/x"),
        SearchResult("", "https://b.example/"),
        SearchResult("No url", ""),
        SearchResult("JS", "javascript:alert(1)"),
        SearchResult("Relative", "/path"),
        SearchResult("Second", "https://c.example/"),
    ]
    out = clean_results(raw, limit=10)
    assert [(r.title, r.url, r.snippet) for r in out] == [
        ("Good one", "https://a.example/x", "snippet & more"), ("Second", "https://c.example/", "")]
    assert len(clean_results(raw, limit=1)) == 1


async def test_tavily_request_and_mapping(server, keyed):
    server.route("/search", Response(js({"query": "q", "results": [
        {"title": "T1", "url": "https://one.example/", "content": "c1", "score": 0.9, "published_date": "2026-11-01"},
        {"title": "T2", "url": "https://two.example/", "content": "c2"},
    ]}), headers=JSON))
    out = await tavily.search(keyed, keyed.settings, "python news", 5)
    assert [(r.title, r.snippet, r.published) for r in out] == [("T1", "c1", "2026-11-01"), ("T2", "c2", None)]
    req = server.requests[-1]
    assert req.method == "POST" and json.loads(req.body) == {"query": "python news", "max_results": 5}
    assert req.headers["authorization"] == f"Bearer {TAVILY_KEY}"
    assert not any(r.path == "/robots.txt" for r in server.requests)  # keyed API call: no robots fetch


async def test_brave_request_and_mapping(server, keyed):
    server.route("/res/v1/web/search", Response(js({"web": {"results": [
        {"title": "B1", "url": "https://b1.example/", "description": "<strong>d1</strong>", "page_age": "2026-10-30T10:00:00"},
        {"title": "B2", "url": "https://b2.example/", "description": "d2", "age": "2 days ago"},
    ]}}), headers=JSON))
    out = await brave.search(keyed, keyed.settings, "rust", 3)
    assert [(r.title, r.snippet, r.published) for r in out] == [("B1", "d1", "2026-10-30"), ("B2", "d2", None)]
    req = server.requests[-1]
    assert req.path.startswith("/res/v1/web/search?") and "q=rust" in req.path and "count=3" in req.path
    assert req.headers["x-subscription-token"] == BRAVE_KEY


@pytest.mark.parametrize("mod,path", [(tavily, "/search"), (brave, "/res/v1/web/search")])
async def test_auth_error_never_contains_key(server, keyed, mod, path, caplog):
    server.route(path, Response(js({"detail": f"invalid key {TAVILY_KEY} {BRAVE_KEY}"}), status=401, headers=JSON))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(PageMintError) as e:
        await mod.search(keyed, keyed.settings, "x", 3)
    assert "401" in e.value.message
    for key in (TAVILY_KEY, BRAVE_KEY):
        assert key not in e.value.message and key not in caplog.text


async def test_rate_limited(server, keyed):
    server.route("/search", Response("{}", status=429, headers=JSON))
    with pytest.raises(PageMintError, match="429"):
        await tavily.search(keyed, keyed.settings, "x", 3)


async def test_keyed_redirect_not_followed(server, keyed):
    server.route("/search", Response(b"", status=307, headers={"Location": "/stolen"}))
    server.route("/stolen", Response("{}", headers=JSON))
    with pytest.raises(PageMintError, match="redirect"):
        await tavily.search(keyed, keyed.settings, "x", 3)
    assert server.hits("/stolen") == 0


async def test_missing_key_is_clear_error(server):
    async with PageMint(make_settings()) as c:
        with pytest.raises(PageMintError, match="PAGEMINT_TAVILY_API_KEY"):
            await tavily.search(c, c.settings, "x", 3)


async def test_searxng_with_strict_client(server):
    server.route("/search", Response(js({"results": [
        {"title": "S1", "url": "https://s1.example/", "content": "c", "engine": "x", "publishedDate": "2026-11-02T00:00:00"},
        {"title": "S2", "url": "https://s2.example/", "content": "c2"},
    ]}), headers=JSON))
    async with PageMint(make_settings(allow_private_networks=False, searxng_url=server.url(""))) as c:
        out = await searxng.search(c, c.settings, "hello world", 1)
        assert [(r.title, r.published) for r in out] == [("S1", "2026-11-02")]
        # the exception ended with the call: the same origin is blocked again for other requests
        r = await c.scrape(server.url("/search"))
        assert r["error"]["type"] == "blocked"
    req = [x for x in server.requests if x.path.startswith("/search")][0]
    assert "q=hello" in req.path and "format=json" in req.path


async def test_searxng_json_disabled_hint(server):
    server.route("/search", Response("<html>403 Forbidden</html>", status=403))
    async with PageMint(make_settings(searxng_url=server.url(""))) as c:
        with pytest.raises(PageMintError) as e:
            await searxng.search(c, c.settings, "x", 3)
    assert "formats" in e.value.message and "json" in e.value.message


async def test_searxng_unreachable(server):
    async with PageMint(make_settings(searxng_url="http://127.0.0.1:1")) as c:
        with pytest.raises(PageMintError, match="SearXNG"):
            await searxng.search(c, c.settings, "x", 3)
