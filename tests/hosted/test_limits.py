import asyncio
import json
import time
from datetime import datetime, timezone

import pytest
from mcp import Client

from conftest import make_settings
from pagemint.client import PageMint
from pagemint.fetcher import FetchResult
from pagemint.hosted.limits import CURRENT_USER, LimitReached, Limits, RenderGate
from pagemint.hosted.store import HostedStore
from pagemint.server import build_server


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def limits(tmp_path):
    return Limits(HostedStore(str(tmp_path / "h.db")), clock=Clock())


def test_page_limit_boundary(limits):
    limits.charge("u1", "pages", 199)
    limits.check("u1", "pages")          # the 200th page is allowed
    limits.charge("u1", "pages", 1)
    with pytest.raises(LimitReached) as e:
        limits.check("u1", "pages")
    assert e.value.type == "limit_reached" and "200" in e.value.message and "UTC" in e.value.message
    assert limits.remaining("u1", "pages") == 0 and limits.remaining("u2", "pages") == 200


def test_server_wide_cap(limits, monkeypatch):
    import pagemint.hosted.limits as mod

    monkeypatch.setitem(mod.GLOBAL_LIMITS, "pages", 300)
    limits.charge("u1", "pages", 200)
    limits.charge("u2", "pages", 100)
    with pytest.raises(LimitReached) as e:
        limits.check("u3", "pages")
    assert "everyone" in e.value.message


def test_resets_next_day(limits):
    limits.charge("u1", "web_searches", 50)
    assert limits.remaining("u1", "web_searches") == 0
    limits.clock.now = limits.clock.now.replace(day=20)
    assert limits.remaining("u1", "web_searches") == 50


async def test_concurrency_guard(limits):
    release = asyncio.Event()

    async def hold():
        async with limits.guard("u1"):
            await release.wait()

    tasks = [asyncio.create_task(hold()) for _ in range(2)]
    await asyncio.sleep(0.05)
    with pytest.raises(LimitReached, match="at once"):
        async with limits.guard("u1"):
            pass
    async with limits.guard("u2"):  # another user is not blocked
        pass
    release.set()
    await asyncio.gather(*tasks)
    async with limits.guard("u1"):  # free again
        pass


# ---- through the MCP tools ----

def page(n_links=0):
    links = "".join(f"<a href='/p{i}'>p{i}</a>" for i in range(n_links))
    return f"<html><body><main><p>{'words ' * 50}</p>{links}</main></body></html>"


async def call(c, tool, args):
    return json.loads((await c.call_tool(tool, args)).content[0].text)


async def test_crawl_stops_at_remaining_budget(server, limits):
    server.route("/p0", page(30))
    for i in range(1, 30):
        server.route(f"/p{i}", page())
    limits.charge("local", "pages", 190)  # 10 left
    srv = build_server(client_factory=lambda: PageMint(make_settings()), limits=limits)
    async with Client(srv) as c:
        r = await call(c, "crawl", {"url": server.url("/p0"), "max_pages": 50})
    assert len(r["pages"]) == 10
    assert any("daily page limit" in w for w in r["warnings"])
    assert limits.remaining("local", "pages") == 0
    async with Client(srv) as c:
        r2 = await call(c, "scrape", {"url": server.url("/p1")})
    assert r2["error"]["type"] == "limit_reached"


async def test_hosted_crawl_cap(server, limits):
    server.route("/p0", page(60))
    for i in range(1, 60):
        server.route(f"/p{i}", page())
    srv = build_server(client_factory=lambda: PageMint(make_settings()), limits=limits)
    async with Client(srv) as c:
        r = await call(c, "crawl", {"url": server.url("/p0"), "max_pages": 100, "max_depth": 1})
    assert len(r["pages"]) == 50


async def test_job_search_and_web_search_counted(server, limits):
    srv = build_server(client_factory=lambda: PageMint(make_settings()), limits=limits)
    async with Client(srv) as c:
        await call(c, "search_jobs", {"query": "python", "sources": "companies"})
        await call(c, "search", {"query": "x"})  # not configured, still counted as an attempt? no
    assert limits.remaining("local", "job_searches") == 99
    assert limits.remaining("local", "web_searches") == 50  # a search that could not run costs nothing


# ---- browser renders ----

class SlowRenderer:
    def __init__(self):
        self.spans = []

    async def render(self, url):
        start = time.monotonic()
        await asyncio.sleep(0.2)
        self.spans.append((start, time.monotonic()))
        html = "<html><body><main><p>" + "rendered " * 50 + "</p></main></body></html>"
        return FetchResult(url=url, status_code=200, headers={}, content=html.encode(), content_type="text/html", text=html)

    async def aclose(self):
        pass


async def test_renders_one_at_a_time_and_counted(server, limits):
    server.route("/a", page())
    server.route("/b", page())
    r = SlowRenderer()
    async with PageMint(make_settings(), renderer=r) as c:
        c.render_gate = RenderGate(limits)
        token = CURRENT_USER.set("u1")
        try:
            out = await asyncio.gather(c.scrape(server.url("/a"), render_js=True), c.scrape(server.url("/b"), render_js=True))
        finally:
            CURRENT_USER.reset(token)
    assert all(o.get("rendered_js") for o in out), out
    (s1, e1), (s2, e2) = sorted(r.spans)
    assert s2 >= e1 - 0.01  # never overlapping
    assert limits.remaining("u1", "renders") == 28


async def test_auto_fallback_skipped_without_render_budget(server, limits):
    server.route("/thin", "<html><body><div id='app'></div></body></html>")
    r = SlowRenderer()
    limits.charge("u1", "renders", 30)
    async with PageMint(make_settings(js_fallback_chars=200), renderer=r) as c:
        c.render_gate = RenderGate(limits)
        token = CURRENT_USER.set("u1")
        try:
            out = await c.scrape(server.url("/thin"))
        finally:
            CURRENT_USER.reset(token)
    assert r.spans == [] and out["rendered_js"] is False
    assert any("limit" in w for w in out["warnings"])


async def test_company_not_found_still_counts(server, limits, monkeypatch):
    from pagemint.jobs.readers import ashby, greenhouse, lever

    root = server.url("").rstrip("/")
    monkeypatch.setattr(greenhouse, "BASE", root)
    monkeypatch.setattr(ashby, "BASE", root)
    monkeypatch.setattr(lever, "HOST_OVERRIDE", root)
    srv = build_server(client_factory=lambda: PageMint(make_settings()), limits=limits)
    async with Client(srv) as c:
        r = await call(c, "company_jobs", {"company": "Nobody Here"})
    assert r["error"]["type"] == "not_found"
    assert limits.remaining("local", "job_searches") == 99
