"""The PageMint client: one object that owns settings, HTTP, limits and caches."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from pagemint.config import MAX_CRAWL_DEPTH, MAX_CRAWL_PAGES, MAX_MAP_LIMIT, Settings
from pagemint.errors import InvalidArgumentError, InvalidURLError, PageMintError, error_dict
from pagemint.extract import extract_main
from pagemint.fetcher import Fetcher, FetchResult
from pagemint.links import links_from_doc, normalize_url, same_site
from pagemint.markdown import to_markdown
from pagemint.sitemap import parse_sitemap

log = logging.getLogger("pagemint")

FORMATS = ("markdown", "html", "links")
ProgressFn = Callable[[int, int, str], Awaitable[None]]

# Links with these extensions are not pages; crawl does not follow them.
_SKIP_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|bmp|tiff?|pdf|zip|gz|tgz|bz2|xz|7z|rar|tar|exe|dmg|msi|apk|iso|bin|"
    r"mp3|mp4|m4a|wav|ogg|webm|avi|mov|mkv|flv|css|js|mjs|json|xml|rss|atom|woff2?|ttf|otf|eot|"
    r"docx?|xlsx?|pptx?|odt|ods|csv)$",
    re.I,
)
MAX_SITEMAP_FILES = 50
MAX_SITEMAP_NESTING = 3


def _looks_like_file(url: str) -> bool:
    return bool(_SKIP_EXT_RE.search(urlsplit(url).path))


class PageMint:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.fetcher = Fetcher(self.settings)

    async def __aenter__(self) -> "PageMint":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.fetcher.aclose()

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _check_url(url: str) -> str:
        norm = normalize_url(url or "")
        if norm is None:
            raise InvalidURLError(f"Invalid URL '{url}'. Only absolute http:// and https:// URLs are supported.")
        return norm

    def _build_page(self, res: FetchResult, format: str) -> dict:
        warnings: list[str] = []
        if not 200 <= res.status_code < 300:
            warnings.append(f"The server answered HTTP {res.status_code}; content may be an error page.")
        if not res.is_html:
            return {
                "url": res.url, "status_code": res.status_code, "title": None,
                "content": "" if format == "links" else res.text, "links": [],
                "rendered_js": False, "warnings": warnings, "_text_len": len(res.text.strip()),
            }
        ex = extract_main(res.text, res.url)
        links = links_from_doc(ex.doc, res.url) if ex.doc is not None else []
        if format == "markdown":
            content = to_markdown(ex.main_html, res.url)
        elif format == "html":
            content = ex.main_html
        else:
            content = ""
        return {
            "url": res.url, "status_code": res.status_code, "title": ex.title, "content": content,
            "links": links, "rendered_js": False, "warnings": warnings, "_text_len": ex.text_len,
        }

    async def _get_page(self, url: str, format: str, render_js: bool) -> dict:
        """Fetch and convert one page. Raises PageMintError on failure."""
        res = await self.fetcher.fetch(url)
        return self._build_page(res, format)

    # ---- operations ----------------------------------------------------

    async def scrape(self, url: str, format: str = "markdown", render_js: bool = False) -> dict:
        try:
            if format not in FORMATS:
                raise InvalidArgumentError(f"Unknown format '{format}'. Use one of: {', '.join(FORMATS)}.")
            target = self._check_url(url)
            page = await self._get_page(target, format, render_js)
            page.pop("_text_len", None)
            return page
        except Exception as e:  # never let one page crash the caller
            if not isinstance(e, PageMintError):
                log.exception("scrape failed for %s", url)
            return {"url": url, **error_dict(e)}

    async def crawl(
        self,
        url: str,
        max_pages: int = 20,
        max_depth: int = 2,
        same_domain: bool = True,
        render_js: bool = False,
        on_progress: ProgressFn | None = None,
    ) -> dict:
        try:
            return await self._crawl(url, max_pages, max_depth, same_domain, render_js, on_progress)
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("crawl failed for %s", url)
            return {"start_url": url, **error_dict(e)}

    async def _crawl(self, url, max_pages, max_depth, same_domain, render_js, on_progress) -> dict:
        s = self.settings
        start = self._check_url(url)
        await self.fetcher.check_host(start)
        warnings: list[str] = []
        if max_pages > MAX_CRAWL_PAGES:
            warnings.append(f"max_pages lowered from {max_pages} to the limit of {MAX_CRAWL_PAGES}.")
            max_pages = MAX_CRAWL_PAGES
        if max_depth > MAX_CRAWL_DEPTH:
            warnings.append(f"max_depth lowered from {max_depth} to the limit of {MAX_CRAWL_DEPTH}.")
            max_depth = MAX_CRAWL_DEPTH
        max_pages = max(1, int(max_pages))
        max_depth = max(0, int(max_depth))

        pages: list[dict] = []
        skipped: list[dict] = []
        stopped_early = False
        total_chars = 0
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        seen: set[str] = {start}
        deadline = time.monotonic() + s.crawl_time_budget

        while queue and len(pages) < max_pages:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stopped_early = True
                warnings.append(f"Stopped after the crawl time limit of {s.crawl_time_budget:g} seconds; returning pages so far.")
                break
            current, depth = queue.popleft()
            try:
                page = await asyncio.wait_for(self._get_page(current, "markdown", render_js), timeout=remaining)
            except asyncio.TimeoutError:
                stopped_early = True
                skipped.append({"url": current, "reason": "time limit"})
                warnings.append(f"Stopped after the crawl time limit of {s.crawl_time_budget:g} seconds; returning pages so far.")
                break
            except PageMintError as e:
                skipped.append({"url": current, "reason": e.message})
                continue

            md = page["content"]
            truncated = len(md) > s.page_char_cap
            if truncated:
                md = md[: s.page_char_cap]
            if total_chars + len(md) > s.result_char_cap:
                stopped_early = True
                skipped.append({"url": page["url"], "reason": "output budget"})
                warnings.append(
                    f"Stopped at the output limit of {s.result_char_cap} characters; use the CLI with --out to save everything."
                )
                break
            total_chars += len(md)
            pages.append({
                "url": page["url"], "status_code": page["status_code"], "title": page["title"],
                "markdown": md, "depth": depth, "truncated": truncated, "rendered_js": page["rendered_js"],
            })
            seen.add(page["url"])  # final URL after redirects
            if on_progress is not None:
                try:
                    await on_progress(len(pages), max_pages, page["url"])
                except Exception:  # progress is best-effort
                    log.debug("progress callback failed", exc_info=True)

            if depth >= max_depth:
                continue
            for link in page["links"]:
                if link in seen:
                    continue
                if same_domain and not same_site(link, start):
                    continue
                if _looks_like_file(link):
                    continue
                seen.add(link)
                queue.append((link, depth + 1))

        return {"start_url": start, "pages": pages, "skipped": skipped, "stopped_early": stopped_early, "warnings": warnings}

    async def map_site(self, url: str, limit: int = 500) -> dict:
        try:
            return await self._map(url, limit)
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("map failed for %s", url)
            return {"url": url, **error_dict(e)}

    async def _map(self, url: str, limit: int) -> dict:
        start = self._check_url(url)
        warnings: list[str] = []
        if limit > MAX_MAP_LIMIT:
            warnings.append(f"limit lowered from {limit} to the maximum of {MAX_MAP_LIMIT}.")
            limit = MAX_MAP_LIMIT
        limit = max(1, int(limit))
        found: dict[str, None] = {}
        disallowed: set[str] = set()

        async def add(u: str) -> None:
            n = normalize_url(u)
            if not n or n in found or n in disallowed or not same_site(n, start) or len(found) >= limit:
                return
            if self.settings.respect_robots and not (await self.fetcher.robots.allowed(n))[0]:
                disallowed.add(n)
                return
            found[n] = None

        # 1. Sitemaps: robots.txt "Sitemap:" lines, else /sitemap.xml.
        parts = urlsplit(start)
        # Resolve + SSRF-check the start host before anything else is fetched.
        await self.fetcher.check_host(start)
        robots_maps = await self.fetcher.robots.sitemaps(start) if self.settings.respect_robots else []
        sitemaps = robots_maps or [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
        todo: deque[tuple[str, int]] = deque((u, 0) for u in sitemaps)
        visited: set[str] = set()
        while todo and len(found) < limit and len(visited) < MAX_SITEMAP_FILES:
            sm, level = todo.popleft()
            if sm in visited:
                continue
            visited.add(sm)
            try:
                res = await self.fetcher.fetch(sm, page=False, check_robots=False)
            except PageMintError as e:
                warnings.append(f"Could not read sitemap {sm}: {e.message}")
                continue
            if res.status_code != 200:
                continue
            urls, children = parse_sitemap(res.content)
            for u in urls:
                await add(u)
            if level < MAX_SITEMAP_NESTING:
                todo.extend((c, level + 1) for c in children)
        from_sitemap = len(found)

        # 2. Links on the start page, if the sitemap did not fill the limit.
        from_links = 0
        if len(found) < limit:
            try:
                page = await self._get_page(start, "links", False)
                before = len(found)
                await add(page["url"])
                for link in page["links"]:
                    await add(link)
                from_links = len(found) - before
            except PageMintError as e:
                warnings.append(f"Could not read the start page for links: {e.message}")

        if disallowed:
            warnings.append(f"Left out {len(disallowed)} URL(s) that robots.txt disallows.")
        source = "sitemap+links" if from_sitemap and from_links else ("sitemap" if from_sitemap else "links")
        return {"url": start, "urls": list(found), "source": source, "warnings": warnings}
