"""The ``pagemint`` command.

    pagemint                  run the MCP server on stdio (what ``uvx pagemint`` does)
    pagemint serve            same, explicit
    pagemint scrape <url>     print one page as Markdown
    pagemint crawl <url>      print (or save) several pages
    pagemint map <url>        print a site's URLs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from pagemint import __version__
from pagemint.client import PageMint
from pagemint.config import Settings

_SLUG_RE = re.compile(r"[^A-Za-z0-9.-]+")
NO_CAP = 10**12


def slug_for(url: str) -> str:
    """A safe file name for a page URL, e.g. ``example.com_docs_intro.md``."""
    p = urlsplit(url)
    path = p.path.strip("/") or "index"
    if p.query:
        path += "_" + p.query
    name = _SLUG_RE.sub("_", f"{p.netloc}_{path}").strip("_")
    return name[:180] + ".md"


def _common(parser: argparse.ArgumentParser, top: bool = False) -> None:
    # Subcommands repeat these options without defaults, so a value given
    # before the subcommand ("pagemint --allow-private scrape URL") is kept.
    d = {} if top else {"default": argparse.SUPPRESS}
    g = parser.add_argument_group("politeness and safety")
    g.add_argument("--rate-limit", type=float, metavar="SECONDS",
                   help="minimum seconds between requests to the same site (default 1)", **d)
    g.add_argument("--timeout", type=float, metavar="SECONDS", help="read timeout per request (default 20)", **d)
    g.add_argument("--allow-private", action="store_true",
                   help="allow localhost and private network addresses (off by default)", **d)
    g.add_argument("--browser", metavar="PATH", help="path to a Chrome/Chromium binary for JavaScript rendering", **d)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pagemint",
        description="Turn web pages into clean Markdown. With no command, runs the MCP server on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"pagemint {__version__}")
    _common(parser, top=True)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="run the MCP server on stdio")
    _common(p)

    p = sub.add_parser("scrape", help="fetch one page")
    p.add_argument("url")
    p.add_argument("--format", choices=["markdown", "html", "links"], default="markdown")
    p.add_argument("--js", action="store_true", help="render with a headless browser")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _common(p)

    p = sub.add_parser("crawl", help="crawl pages starting from a URL")
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--any-domain", action="store_true", help="also follow links to other sites")
    p.add_argument("--js", action="store_true", help="render every page with a headless browser")
    p.add_argument("--out", metavar="DIR", help="save one .md file per page here (no size limits)")
    p.add_argument("--time-limit", type=float, metavar="SECONDS", help="stop after this long (default 120)")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _common(p)

    p = sub.add_parser("search", help="search the web with your SearXNG or your own API key")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10, help="number of results (max 20)")
    p.add_argument("--fetch", type=int, default=0, metavar="N", help="also read the top N result pages (max 5)")
    p.add_argument("--provider", default="auto", choices=["auto", "searxng", "tavily", "brave"])
    p.add_argument("--searxng-url", metavar="URL", help="your SearXNG, e.g. http://localhost:8888 (keys: env vars only)")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _common(p)

    p = sub.add_parser("map", help="list URLs on a site")
    p.add_argument("url")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _common(p)
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict = {}
    if args.rate_limit is not None:
        overrides["rate_limit"] = args.rate_limit
    if args.timeout is not None:
        overrides["read_timeout"] = args.timeout
        overrides["connect_timeout"] = min(10.0, args.timeout)
    if args.allow_private:
        overrides["allow_private_networks"] = True
    if args.browser:
        overrides["browser_executable"] = args.browser
    if getattr(args, "out", None):
        overrides["page_char_cap"] = NO_CAP
        overrides["result_char_cap"] = NO_CAP
    if getattr(args, "searxng_url", None):
        overrides["searxng_url"] = args.searxng_url
    if getattr(args, "time_limit", None):
        overrides["crawl_time_budget"] = args.time_limit
    return Settings.from_env(**overrides)


def run_server(settings: Settings) -> None:
    from pagemint.server import build_server

    build_server(client_factory=lambda: PageMint(settings)).run("stdio")


def _fail(result: dict) -> int:
    err = result["error"]
    print(f"pagemint: {err['type']}: {err['message']}", file=sys.stderr)
    return 1


def _warn(result: dict) -> None:
    for w in result.get("warnings", []):
        print(f"pagemint: warning: {w}", file=sys.stderr)


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    async with PageMint(settings) as client:
        if args.command == "scrape":
            r = await client.scrape(args.url, format=args.format, render_js=args.js)
            if "error" in r:
                return _fail(r)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2))
            elif args.format == "links":
                print("\n".join(r["links"]))
            else:
                print(r["content"], end="" if r["content"].endswith("\n") else "\n")
            _warn(r)
            return 0

        if args.command == "crawl":
            r = await client.crawl(
                args.url, max_pages=args.max_pages, max_depth=args.max_depth,
                same_domain=not args.any_domain, render_js=args.js,
            )
            if "error" in r:
                return _fail(r)
            if args.out:
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                for page in r["pages"]:
                    (out / slug_for(page["url"])).write_text(
                        f"<!-- source: {page['url']} -->\n\n{page['markdown']}", encoding="utf-8"
                    )
                print(f"pagemint: saved {len(r['pages'])} pages to {out}", file=sys.stderr)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2))
            elif not args.out:
                for page in r["pages"]:
                    print(f"<!-- source: {page['url']} -->\n\n{page['markdown']}\n")
            for s in r["skipped"]:
                print(f"pagemint: skipped {s['url']}: {s['reason']}", file=sys.stderr)
            _warn(r)
            return 0

        if args.command == "search":
            from pagemint.search.service import run_search

            r = await run_search(client, args.query, limit=args.limit, fetch_pages=args.fetch, provider=args.provider)
            if "error" in r:
                return _fail(r)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2))
            else:
                for item in r["results"]:
                    print(f"{item['title']} — {item['url']}")
                    if item["snippet"]:
                        print(f"    {item['snippet']}")
                for page in r.get("pages", []):
                    if "markdown" in page:
                        print(f"\n<!-- source: {page['url']} -->\n\n{page['markdown']}")
                    else:
                        print(f"pagemint: could not read {page['url']}: {page['error']['message']}", file=sys.stderr)
            _warn(r)
            return 0

        if args.command == "map":
            r = await client.map_site(args.url, limit=args.limit)
            if "error" in r:
                return _fail(r)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2))
            else:
                print("\n".join(r["urls"]))
            _warn(r)
            return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="pagemint: %(levelname)s: %(message)s")
    settings = settings_from_args(args)
    if args.command in (None, "serve"):
        run_server(settings)
        return 0
    try:
        return asyncio.run(_run(args, settings))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
