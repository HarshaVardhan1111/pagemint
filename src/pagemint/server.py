"""MCP server exposing PageMint's tools."""

from __future__ import annotations

import json
import logging
import sys
from typing import Callable, Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

from pagemint import __version__
from pagemint.client import PageMint

INSTRUCTIONS = (
    "PageMint turns web pages into clean Markdown. Use `scrape` for one page, `crawl` to read several "
    "linked pages from a site, and `map` to list a site's URLs. It respects robots.txt and rate limits; "
    "blocked URLs come back with the reason."
)


def _result(data: dict) -> CallToolResult:
    is_error = "error" in data
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))],
        structured_content=data,
        is_error=is_error,
    )


def build_server(client_factory: Callable[[], PageMint] = PageMint) -> MCPServer:
    server = MCPServer(name="pagemint", version=__version__, instructions=INSTRUCTIONS)
    holder: dict[str, PageMint] = {}

    def client() -> PageMint:
        if "c" not in holder:
            holder["c"] = client_factory()
        return holder["c"]

    @server.tool()
    async def scrape(
        url: str,
        format: Literal["markdown", "html", "links"] = "markdown",
        render_js: bool = False,
    ) -> CallToolResult:
        """Fetch one web page and return its main content.

        Returns title, content (Markdown by default; cleaned main HTML with format="html"; empty with
        format="links"), all links on the page, and the HTTP status code. Set render_js=True for pages
        that need JavaScript (needs the optional browser install).
        """
        return _result(await client().scrape(url, format=format, render_js=render_js))

    server._pagemint_holder = holder  # type: ignore[attr-defined]
    return server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    build_server().run("stdio")
