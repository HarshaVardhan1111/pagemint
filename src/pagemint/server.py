"""MCP server exposing PageMint's tools."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Callable, Literal

from mcp.server.mcpserver import Context, MCPServer
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
    holder: dict[str, PageMint] = {}

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:  # close HTTP connections and the browser on shutdown
            c = holder.pop("c", None)
            if c is not None:
                await c.aclose()

    server = MCPServer(name="pagemint", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

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

    @server.tool()
    async def crawl(
        url: str,
        max_pages: int = 20,
        max_depth: int = 2,
        same_domain: bool = True,
        render_js: bool = False,
        ctx: Context | None = None,
    ) -> CallToolResult:
        """Crawl a site starting at url and return each page as Markdown.

        Follows links breadth-first up to max_depth link hops (limit 5) and max_pages pages (limit 100).
        same_domain=True stays on the starting host. Skipped URLs (robots.txt, blocked, errors) are listed
        with the reason. Long crawls stop at a time/size budget and return the pages collected so far.
        """

        async def progress(done: int, total: int, page_url: str) -> None:
            if ctx is not None:
                await ctx.report_progress(done, total, f"Crawled {page_url}")

        return _result(
            await client().crawl(
                url, max_pages=max_pages, max_depth=max_depth, same_domain=same_domain,
                render_js=render_js, on_progress=progress,
            )
        )

    @server.tool(name="map")
    async def map_site(url: str, limit: int = 500) -> CallToolResult:
        """List URLs on a website (sitemap.xml first, then links on the start page).

        Returns up to limit URLs (maximum 5000) on the same host, without fetching each page.
        """
        return _result(await client().map_site(url, limit=limit))

    return server
