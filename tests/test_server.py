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
    assert {"scrape"} <= names


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
