import json

from mcp import Client

from conftest import make_settings
from pagemint.client import PageMint
from pagemint.server import build_server


def _server():
    return build_server(client_factory=lambda: PageMint(make_settings()))


async def test_mcp_tools_registered():
    async with Client(_server()) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert {"scrape", "crawl", "map"} <= names


async def test_mcp_crawl_and_map(server):
    server.route("/", "<html><body><main><p>Home page text</p><a href='/a'>a</a></main></body></html>")
    server.route("/a", "<html><body><main><p>Page A text</p></main></body></html>")
    async with Client(_server()) as c:
        cr = json.loads((await c.call_tool("crawl", {"url": server.url("/"), "max_pages": 2})).content[0].text)
        mp = json.loads((await c.call_tool("map", {"url": server.url("/")})).content[0].text)
    assert len(cr["pages"]) == 2
    assert server.url("/a") in mp["urls"]


async def test_mcp_scrape_returns_result(server):
    server.route("/p", "<html><head><title>T</title></head><body><main><p>Hello MCP world</p></main></body></html>")
    async with Client(_server()) as c:
        res = await c.call_tool("scrape", {"url": server.url("/p")})
    assert not res.is_error
    data = json.loads(res.content[0].text)
    assert data["title"] == "T" and "Hello MCP world" in data["content"]


async def test_mcp_error_marked_is_error():
    async with Client(_server()) as c:
        res = await c.call_tool("scrape", {"url": "ftp://example.com/"})
    assert res.is_error
    assert json.loads(res.content[0].text)["error"]["type"] == "invalid_url"


async def test_stdio_server_end_to_end(server):
    """Start `python -m pagemint` as a real subprocess and talk MCP over stdio."""
    import os
    import sys

    from mcp import StdioServerParameters

    server.route("/", "<html><head><title>E2E</title></head><body><main><p>Over stdio</p></main></body></html>")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pagemint", "--allow-private", "--rate-limit", "0"],
        env={**os.environ, "PAGEMINT_JS_FALLBACK_CHARS": "0"},
    )
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        res = await c.call_tool("scrape", {"url": server.url("/")})
    assert {"scrape", "crawl", "map"} <= names
    assert json.loads(res.content[0].text)["title"] == "E2E"


async def test_mcp_job_tools(server, tmp_path, monkeypatch):
    monkeypatch.setenv("PAGEMINT_DATA_DIR", str(tmp_path))
    async with Client(_server()) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        res = await c.call_tool("saved_companies", {"action": "list"})
    assert {"search_jobs", "company_jobs", "check_job", "saved_companies"} <= names
    assert json.loads(res.content[0].text)["count"] == 0
    assert (tmp_path / "jobs.sqlite3").exists()
