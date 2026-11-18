"""The PageMint client: one object that owns settings, HTTP, limits and caches."""

from __future__ import annotations

import logging

from pagemint.config import Settings
from pagemint.errors import InvalidArgumentError, InvalidURLError, error_dict
from pagemint.extract import extract_main
from pagemint.fetcher import Fetcher, FetchResult
from pagemint.links import links_from_doc, normalize_url
from pagemint.markdown import to_markdown

log = logging.getLogger("pagemint")

FORMATS = ("markdown", "html", "links")


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

    # ---- operations ----------------------------------------------------

    async def scrape(self, url: str, format: str = "markdown", render_js: bool = False) -> dict:
        try:
            if format not in FORMATS:
                raise InvalidArgumentError(f"Unknown format '{format}'. Use one of: {', '.join(FORMATS)}.")
            target = self._check_url(url)
            res = await self.fetcher.fetch(target)
            page = self._build_page(res, format)
            page.pop("_text_len", None)
            return page
        except Exception as e:  # never let one page crash the caller
            if not hasattr(e, "type"):
                log.exception("scrape failed for %s", url)
            return {"url": url, **error_dict(e)}
