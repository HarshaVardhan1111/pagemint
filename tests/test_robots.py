from conftest import Response, make_settings
from pagemint.client import PageMint
from pagemint.robots import parse_robots

from pagemint import __version__

UA = f"PageMint/{__version__} (+https://github.com/HarshaVardhan1111/pagemint)"


def rules(text: str):
    return parse_robots(text).rules_for(UA)


def test_disallow_and_allow_longest_match():
    r = rules("User-agent: *\nDisallow: /private\nAllow: /private/public\n")
    assert r.check("/private/x") == (False, "Disallow: /private")
    assert r.check("/private/public/page")[0] is True
    assert r.check("/other")[0] is True


def test_allow_wins_tie():
    r = rules("User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert r.check("/page")[0] is True


def test_wildcards_and_end_anchor():
    r = rules("User-agent: *\nDisallow: /*.pdf$\nDisallow: /search*q=\n")
    assert r.check("/files/a.pdf")[0] is False
    assert r.check("/files/a.pdf?x=1")[0] is True
    assert r.check("/search?lang=en&q=test")[0] is False


def test_specific_agent_group_beats_star():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: pagemint\nDisallow: /admin\n"
    r = rules(text)
    assert r.check("/blog")[0] is True
    assert r.check("/admin/x")[0] is False


def test_agent_match_is_case_insensitive_and_groups_merge():
    text = "User-agent: Googlebot\nUser-agent: PAGEMINT\nDisallow: /a\n\nUser-agent: PageMint/2.0\nDisallow: /b\n"
    r = rules(text)
    assert r.check("/a")[0] is False and r.check("/b")[0] is False


def test_empty_disallow_allows_everything_and_comments_ignored():
    r = rules("# comment\nUser-agent: * # all\nDisallow:\n")
    assert r.check("/anything")[0] is True


def test_no_matching_group_allows():
    assert rules("User-agent: otherbot\nDisallow: /\n").check("/x")[0] is True


def test_crawl_delay_and_sitemaps():
    f = parse_robots("Sitemap: http://h/s1.xml\nUser-agent: *\nCrawl-delay: 2.5\nDisallow: /x\nsitemap: http://h/s2.xml\n")
    assert f.rules_for(UA).crawl_delay == 2.5
    assert f.sitemaps == ["http://h/s1.xml", "http://h/s2.xml"]


def test_percent_encoding_normalized():
    r = rules("User-agent: *\nDisallow: /caf%C3%A9\n")
    assert r.check("/café")[0] is False


async def test_scrape_disallowed_reports_rule(server, client):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /secret\n", headers={"Content-Type": "text/plain"}))
    server.route("/secret/page", "<html><body>hidden</body></html>")
    r = await client.scrape(server.url("/secret/page"))
    assert r["error"]["type"] == "robots"
    assert "robots.txt" in r["error"]["message"] and "Disallow: /secret" in r["error"]["message"]
    assert server.hits("/secret/page") == 0


async def test_robots_404_allows(server, client):
    del server.routes["/robots.txt"]
    server.route("/p", "<html><body><main><p>ok page</p></main></body></html>")
    assert "error" not in await client.scrape(server.url("/p"))


async def test_robots_5xx_disallows(server, client):
    server.route("/robots.txt", Response("oops", status=503, headers={"Content-Type": "text/plain"}))
    server.route("/p", "<html><body>x</body></html>")
    r = await client.scrape(server.url("/p"))
    assert r["error"]["type"] == "robots"
    assert "503" in r["error"]["message"]


async def test_robots_timeout_disallows(server):
    server.route("/robots.txt", Response("User-agent: *\nAllow: /\n", delay=1.0, headers={"Content-Type": "text/plain"}))
    server.route("/p", "<html><body>x</body></html>")
    async with PageMint(make_settings(read_timeout=0.3)) as c:
        r = await c.scrape(server.url("/p"))
    assert r["error"]["type"] == "robots"
    assert server.hits("/p") == 0


async def test_robots_fetched_once_per_host(server, client):
    server.route("/a", "<html><body>a</body></html>")
    server.route("/b", "<html><body>b</body></html>")
    await client.scrape(server.url("/a"))
    await client.scrape(server.url("/b"))
    assert server.hits("/robots.txt") == 1


async def test_crawl_skips_disallowed_with_reason(server, client):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /no\n", headers={"Content-Type": "text/plain"}))
    server.route("/", "<html><body><main><p>start</p><a href='/no/x'>n</a><a href='/yes'>y</a></main></body></html>")
    server.route("/yes", "<html><body><main><p>yes</p></main></body></html>")
    r = await client.crawl(server.url("/"))
    assert [p["url"] for p in r["pages"]] == [server.url("/"), server.url("/yes")]
    assert any(s["url"] == server.url("/no/x") and "robots.txt" in s["reason"] for s in r["skipped"])


async def test_redirect_to_disallowed_path_blocked(server, client):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /private\n", headers={"Content-Type": "text/plain"}))
    server.route("/go", Response(b"", status=302, headers={"Location": "/private/x"}))
    r = await client.scrape(server.url("/go"))
    assert r["error"]["type"] == "robots"
    assert server.hits("/private/x") == 0


async def test_map_filters_disallowed_urls(server, client):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /hidden\n", headers={"Content-Type": "text/plain"}))
    server.route("/", "<html><body><a href='/hidden/a'>h</a><a href='/shown'>s</a></body></html>")
    r = await client.map_site(server.url("/"))
    assert server.url("/shown") in r["urls"]
    assert not any("/hidden" in u for u in r["urls"])
    assert any("robots.txt" in w for w in r["warnings"])


async def test_crawl_delay_slows_requests(server):
    server.route("/robots.txt", Response("User-agent: *\nCrawl-delay: 0.4\n", headers={"Content-Type": "text/plain"}))
    server.route("/a", "<html><body>a</body></html>")
    server.route("/b", "<html><body>b</body></html>")
    async with PageMint(make_settings(rate_limit=0.0)) as c:
        await c.scrape(server.url("/a"))
        await c.scrape(server.url("/b"))
    times = [r.time for r in server.requests if r.path in ("/a", "/b")]
    assert times[1] - times[0] >= 0.35


def test_bom_is_ignored():
    assert rules("﻿User-agent: *\nDisallow: /\n").check("/x")[0] is False


async def test_robots_with_bom_bytes(server, client):
    server.route("/robots.txt", Response(b"\xef\xbb\xbfUser-agent: *\nDisallow: /\n", headers={"Content-Type": "text/plain"}))
    server.route("/p", "<html><body>x</body></html>")
    assert (await client.scrape(server.url("/p")))["error"]["type"] == "robots"


async def test_cancelled_waiter_does_not_break_other_requests(server):
    import asyncio

    server.route("/robots.txt", Response("User-agent: *\nAllow: /\n", delay=0.5, headers={"Content-Type": "text/plain"}))
    server.route("/a", "<html><body><main><p>a page</p></main></body></html>")
    server.route("/b", "<html><body><main><p>b page</p></main></body></html>")
    async with PageMint(make_settings()) as c:
        ta = asyncio.create_task(c.scrape(server.url("/a")))
        tb = asyncio.create_task(c.scrape(server.url("/b")))
        await asyncio.sleep(0.1)
        ta.cancel()
        rb = await tb
    assert "error" not in rb and "b page" in rb["content"]


async def test_robots_429_disallows(server, client):
    server.route("/robots.txt", Response("slow down", status=429, headers={"Content-Type": "text/plain"}))
    server.route("/p", "<html><body>x</body></html>")
    r = await client.scrape(server.url("/p"))
    assert r["error"]["type"] == "robots"
    assert "429" in r["error"]["message"]
