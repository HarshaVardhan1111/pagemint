import pytest

import pagemint.safety as safety
from conftest import Response, make_settings
from pagemint.browser import BrowserRenderer
from pagemint.client import PageMint
from pagemint.errors import BrowserUnavailableError, FetchError
from pagemint.fetcher import FetchResult

THIN = "<html><head><title>App</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"
RICH = "<html><head><title>App</title></head><body><main><h1>Rendered</h1><p>" + "Real content from JavaScript. " * 20 + "</p></main></body></html>"


class FakeRenderer:
    def __init__(self, html=RICH, exc=None):
        self.html, self.exc, self.calls = html, exc, []

    async def render(self, url):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return FetchResult(url=url, status_code=200, headers={}, content=self.html.encode(), content_type="text/html", text=self.html)

    async def aclose(self):
        pass


async def test_fallback_used_when_thin(server):
    server.route("/app", THIN)
    fake = FakeRenderer()
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        r = await c.scrape(server.url("/app"))
    assert r["rendered_js"] is True
    assert "# Rendered" in r["content"]
    assert fake.calls == [server.url("/app")]


async def test_fallback_not_used_when_rich(server):
    server.route("/rich", RICH)
    fake = FakeRenderer()
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        r = await c.scrape(server.url("/rich"))
    assert r["rendered_js"] is False and fake.calls == []


async def test_fallback_not_used_for_error_status(server):
    server.route("/err", Response(THIN, status=500))
    fake = FakeRenderer()
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        await c.scrape(server.url("/err"))
    assert fake.calls == []


async def test_fallback_keeps_plain_if_render_not_better(server):
    server.route("/app", THIN)
    async with PageMint(make_settings(js_fallback_chars=200), renderer=FakeRenderer(html=THIN)) as c:
        r = await c.scrape(server.url("/app"))
    assert r["rendered_js"] is False


async def test_fallback_unavailable_warns(server):
    server.route("/app", THIN)
    fake = FakeRenderer(exc=BrowserUnavailableError("no browser"))
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        r = await c.scrape(server.url("/app"))
    assert "error" not in r and r["rendered_js"] is False
    assert any("playwright install chromium" in w for w in r["warnings"])


async def test_fallback_render_failure_warns(server):
    server.route("/app", THIN)
    async with PageMint(make_settings(js_fallback_chars=200), renderer=FakeRenderer(exc=FetchError("boom"))) as c:
        r = await c.scrape(server.url("/app"))
    assert "error" not in r and any("boom" in w for w in r["warnings"])


async def test_render_js_true_forces_browser(server):
    server.route("/rich", RICH)
    fake = FakeRenderer()
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        r = await c.scrape(server.url("/rich"), render_js=True)
    assert r["rendered_js"] is True and fake.calls


async def test_render_js_true_without_browser_is_error(server):
    server.route("/rich", RICH)
    async with PageMint(make_settings(js_fallback_chars=200), renderer=FakeRenderer(exc=BrowserUnavailableError("no browser"))) as c:
        r = await c.scrape(server.url("/rich"), render_js=True)
    assert r["error"]["type"] == "browser_unavailable"
    assert "playwright install chromium" in r["error"]["message"]


async def test_render_js_still_checks_robots(server):
    server.route("/robots.txt", Response("User-agent: *\nDisallow: /app\n", headers={"Content-Type": "text/plain"}))
    fake = FakeRenderer()
    async with PageMint(make_settings(js_fallback_chars=200), renderer=fake) as c:
        r = await c.scrape(server.url("/app"), render_js=True)
    assert r["error"]["type"] == "robots" and fake.calls == []


async def test_crawl_uses_fallback(server):
    server.route("/app", THIN)
    async with PageMint(make_settings(js_fallback_chars=200), renderer=FakeRenderer()) as c:
        r = await c.crawl(server.url("/app"))
    assert r["pages"][0]["rendered_js"] is True


# ---- real browser (skipped when Playwright/Chromium is not installed) ----

JS_PAGE = """<html><head><title>JS</title></head><body><div id="root"></div>
<script>
document.getElementById('root').innerHTML = '<main><h1>Built by JS</h1><p>' + 'Dynamic paragraph text. '.repeat(20) + '</p></main>';
</script></body></html>"""


@pytest.fixture
async def browser_client():
    c = PageMint(make_settings(js_fallback_chars=200))
    try:
        await c.renderer.start()
    except BrowserUnavailableError as e:
        await c.aclose()
        pytest.skip(f"browser not available: {e.message}")
    yield c
    await c.aclose()


async def test_real_browser_renders_js_page(server, browser_client):
    server.route("/js", JS_PAGE)
    r = await browser_client.scrape(server.url("/js"))
    assert r["rendered_js"] is True, r
    assert "# Built by JS" in r["content"]


async def test_real_browser_blocks_private_subrequests(server, monkeypatch):
    """127.0.0.1 is treated as public; requests the page makes to 'localhost' must be blocked."""
    real = safety.is_blocked_ip
    monkeypatch.setattr(safety, "is_blocked_ip", lambda ip: str(ip) != "127.0.0.1" and real(ip))
    secret = server.url("/secret", host="localhost")
    server.route("/redir", Response(b"", status=302, headers={"Location": secret}))
    server.route("/secret", "secret data")
    page = f"""<html><body><main><p id="out">{'Waiting for results. ' * 15}</p></main>
    <iframe src="/redir"></iframe><script>
      Promise.allSettled([fetch('{secret}'), fetch('/redir'), new Promise(r => {{ const i = new Image(); i.onload = i.onerror = r; i.src = '{secret}?img'; }})])
        .then(() => document.getElementById('out').textContent += ' done');
    </script></body></html>"""
    server.route("/page", page)
    async with PageMint(make_settings(allow_private_networks=False)) as c:
        try:
            await c.renderer.start()
        except BrowserUnavailableError as e:
            pytest.skip(e.message)
        r = await c.scrape(server.url("/page"), render_js=True)
    assert "error" not in r, r
    assert server.hits("/secret") == 0
    assert not any(req.path.startswith("/secret") for req in server.requests)


def test_renderer_unavailable_message():
    msg = BrowserRenderer.install_hint()
    assert 'pip install "pagemint[js]"' in msg and "playwright install chromium" in msg


async def test_real_browser_blocks_redirect_hop_into_private(server, monkeypatch):
    real = safety.is_blocked_ip
    monkeypatch.setattr(safety, "is_blocked_ip", lambda ip: str(ip) != "127.0.0.1" and real(ip))
    server.route("/go", Response(b"", status=302, headers={"Location": server.url("/inside", host="localhost")}))
    server.route("/inside", RICH)
    async with PageMint(make_settings(allow_private_networks=False)) as c:
        try:
            await c.renderer.start()
        except BrowserUnavailableError as e:
            pytest.skip(e.message)
        r = await c.scrape(server.url("/go"), render_js=True)
    assert r["error"]["type"] == "fetch", r
    assert "localhost" in r["error"]["message"]
    assert server.hits("/inside") == 0


async def test_real_browser_follows_safe_redirects(server, browser_client):
    server.route("/go", Response(b"", status=301, headers={"Location": server.url("/landing", host="localhost")}))
    server.route("/landing", "<html><body><main><p>landed " + "word " * 60 + "</p></main><script>fetch('/r2')</script></body></html>")
    server.route("/r2", Response(b"", status=302, headers={"Location": "/final"}))
    server.route("/final", "ok")
    r = await browser_client.scrape(server.url("/go"), render_js=True)
    assert r["url"] == server.url("/landing", host="localhost")
    assert "landed" in r["content"]
    assert server.hits("/final") == 1
