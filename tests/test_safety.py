import asyncio
import ipaddress
import time

import pytest

import pagemint.safety as safety
from conftest import Response, make_settings
from pagemint.client import PageMint
from pagemint.ratelimit import RateLimiter

UA = "PageMint/0.1.0 (+https://github.com/HarshaVardhan1111/pagemint)"


@pytest.fixture
async def strict():
    c = PageMint(make_settings(allow_private_networks=False))
    yield c
    await c.aclose()


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1",
    "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::",
])
def test_blocked_ips(ip):
    assert safety.is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700::1111"])
def test_public_ips_allowed(ip):
    assert not safety.is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("url,etype", [
    ("http://localhost/", "blocked"),
    ("http://127.0.0.1/", "blocked"),
    ("http://10.0.0.1/", "blocked"),
    ("http://169.254.169.254/latest/meta-data/", "blocked"),
    ("http://[::1]/", "blocked"),
    ("http://2130706433/", "blocked"),
    ("http://sub.localhost/", "blocked"),
    ("ftp://example.com/", "invalid_url"),
    ("file:///etc/passwd", "invalid_url"),
    ("javascript:alert(1)", "invalid_url"),
])
async def test_ssrf_blocked(strict, url, etype):
    r = await strict.scrape(url)
    assert r["error"]["type"] == etype, r


async def test_ssrf_blocks_crawl_and_map(strict):
    assert (await strict.crawl("http://127.0.0.1/"))["error"]["type"] == "blocked"
    assert (await strict.map_site("http://127.0.0.1/"))["error"]["type"] == "blocked"


async def test_mixed_dns_answers_blocked():
    async def resolver(host, port):
        return ["93.184.216.34", "10.0.0.5"]

    with pytest.raises(safety.BlockedError):
        await safety.resolve_and_check("evil.test", 80, allow_private=False, resolver=resolver)


async def test_redirect_to_private_blocked(server, strict, monkeypatch):
    """Treat the test server's 127.0.0.1 as public; its redirect into 10.x must still be blocked."""
    real = safety.is_blocked_ip
    monkeypatch.setattr(safety, "is_blocked_ip", lambda ip: str(ip) != "127.0.0.1" and real(ip))
    server.route("/go", Response(b"", status=302, headers={"Location": "http://10.0.0.1/admin"}))
    r = await strict.scrape(server.url("/go"))
    assert r["error"]["type"] == "blocked"
    assert "10.0.0.1" in r["error"]["message"]


async def test_redirect_to_other_scheme_blocked(server, client):
    server.route("/go", Response(b"", status=301, headers={"Location": "file:///etc/passwd"}))
    assert (await client.scrape(server.url("/go")))["error"]["type"] == "invalid_url"


async def test_redirects_followed_and_final_url_reported(server, client):
    server.route("/old", Response(b"", status=301, headers={"Location": "/new"}))
    server.route("/new", "<html><body><main><p>new home</p></main></body></html>")
    r = await client.scrape(server.url("/old"))
    assert r["url"] == server.url("/new") and "new home" in r["content"]


async def test_too_many_redirects(server, client):
    for i in range(8):
        server.route(f"/r{i}", Response(b"", status=302, headers={"Location": f"/r{i + 1}"}))
    r = await client.scrape(server.url("/r0"))
    assert r["error"]["type"] == "fetch" and "redirect" in r["error"]["message"].lower()


async def test_size_limit_content_length(server):
    server.route("/big", Response(b"x" * 2000))
    async with PageMint(make_settings(max_bytes=1000)) as c:
        r = await c.scrape(server.url("/big"))
    assert r["error"]["type"] == "too_large"


async def test_size_limit_chunked(server):
    server.route("/big", Response(b"<p>" + b"x" * 600_000, chunked=True))
    async with PageMint(make_settings(max_bytes=500_000)) as c:
        r = await c.scrape(server.url("/big"))
    assert r["error"]["type"] == "too_large"
    assert "500000" in r["error"]["message"] or "0.5 MB" in r["error"]["message"]


async def test_read_timeout(server):
    server.route("/slow", Response("<p>slow</p>", delay=1.0))
    async with PageMint(make_settings(read_timeout=0.3)) as c:
        r = await c.scrape(server.url("/slow"))
    assert r["error"]["type"] == "timeout"


async def test_user_agent_header(server, client):
    server.route("/p", "<html><body>x</body></html>")
    await client.scrape(server.url("/p"))
    assert all(r.headers["user-agent"] == UA for r in server.requests)


async def test_rate_limiter_same_host():
    rl = RateLimiter(0.2)
    t0 = time.monotonic()
    for _ in range(3):
        await rl.wait("a.example")
    assert time.monotonic() - t0 >= 0.39


async def test_rate_limiter_hosts_independent():
    rl = RateLimiter(0.3)
    t0 = time.monotonic()
    await asyncio.gather(*(rl.wait(h) for h in ["a", "b", "c"]))
    assert time.monotonic() - t0 < 0.2


async def test_client_rate_limits_requests_to_one_host(server):
    for p in ("/a", "/b", "/c"):
        server.route(p, "<html><body>x</body></html>")
    async with PageMint(make_settings(rate_limit=0.25)) as c:
        await asyncio.gather(*(c.scrape(server.url(p)) for p in ("/a", "/b", "/c")))
    times = sorted(r.time for r in server.requests)  # robots.txt + 3 pages
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) == 4 and min(gaps) >= 0.22


async def test_dns_rebinding_blocked_at_connect():
    """The pre-check sees a public address; the connection-time lookup sees a private one."""
    calls = {"n": 0}

    async def flipping_resolver(host, port):
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["10.0.0.1"]

    async with PageMint(make_settings(allow_private_networks=False, respect_robots=False)) as c:
        c.fetcher.resolver = flipping_resolver
        r = await c.scrape("http://rebind.test/")
    assert r["error"]["type"] == "blocked", r
    assert calls["n"] == 2


async def test_respect_robots_not_settable_from_env(monkeypatch):
    from pagemint.config import Settings

    monkeypatch.setenv("PAGEMINT_RESPECT_ROBOTS", "0")
    assert Settings.from_env().respect_robots is True


async def test_unresolvable_name_allowed_only_behind_proxy():
    async def no_dns(host, port):
        raise OSError("Name or service not known")

    with pytest.raises(safety.FetchError):
        await safety.resolve_and_check("example.com", 443, allow_private=False, resolver=no_dns)
    assert await safety.resolve_and_check("example.com", 443, allow_private=False, resolver=no_dns, via_proxy=True) == []
    # Literal private IPs and localhost names stay blocked even behind a proxy.
    with pytest.raises(safety.BlockedError):
        await safety.resolve_and_check("10.0.0.1", 80, allow_private=False, resolver=no_dns, via_proxy=True)
    with pytest.raises(safety.BlockedError):
        await safety.resolve_and_check("localhost", 80, allow_private=False, resolver=no_dns, via_proxy=True)


@pytest.mark.parametrize("host", ["intranet", "db.internal", "printer.local", "nas.lan", "x.corp", "router.home.arpa"])
async def test_proxy_fallback_refuses_internal_looking_names(host):
    async def no_dns(h, port):
        raise OSError("no dns")

    with pytest.raises(safety.BlockedError):
        await safety.resolve_and_check(host, 80, allow_private=False, resolver=no_dns, via_proxy=True)


async def test_post_json_goes_through_checks(server, client):
    import json as _json

    server.route("/api/jobs", Response('{"ok": true}', headers={"Content-Type": "application/json"}))
    status, data = await client.fetcher.fetch_json(server.url("/api/jobs"), method="POST", json_body={"limit": 20})
    assert status == 200 and data == {"ok": True}
    req = [r for r in server.requests if r.path == "/api/jobs"][0]
    assert req.method == "POST" and _json.loads(req.body) == {"limit": 20}
    assert req.headers["content-type"].startswith("application/json")
    assert req.headers["user-agent"] == UA


async def test_post_respects_robots(server, client):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /api\n", headers={"Content-Type": "text/plain"}))
    server.route("/api/jobs", Response("{}", headers={"Content-Type": "application/json"}))
    from pagemint.errors import RobotsError

    with pytest.raises(RobotsError):
        await client.fetcher.fetch_json(server.url("/api/jobs"), method="POST", json_body={})
    assert server.hits("/api/jobs") == 0


async def test_fetch_json_parses_and_rejects_html(server, client):
    from pagemint.errors import FetchError

    server.route("/j", Response('[1, 2]', headers={"Content-Type": "application/json"}))
    server.route("/h", "<html>not json</html>")
    server.route("/missing.json", Response("{}", status=404, headers={"Content-Type": "application/json"}))
    assert await client.fetcher.fetch_json(server.url("/j")) == (200, [1, 2])
    assert (await client.fetcher.fetch_json(server.url("/missing.json")))[0] == 404
    with pytest.raises(FetchError):
        await client.fetcher.fetch_json(server.url("/h"))


async def test_fetch_json_max_bytes_override(server):
    from pagemint.errors import TooLargeError

    server.route("/big.json", Response('["' + "x" * 3000 + '"]', headers={"Content-Type": "application/json"}))
    async with PageMint(make_settings(max_bytes=1000)) as c:
        with pytest.raises(TooLargeError):
            await c.fetcher.fetch_json(server.url("/big.json"))
        status, data = await c.fetcher.fetch_json(server.url("/big.json"), max_bytes=10_000)
    assert status == 200 and len(data[0]) == 3000
