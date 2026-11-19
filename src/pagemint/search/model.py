"""Search result shape and cleaning."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pagemint.jobs.model import html_to_text
from pagemint.links import normalize_url

SNIPPET_CHARS = 500


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def clean_results(raw: list[SearchResult], limit: int) -> list[SearchResult]:
    """Plain-text titles/snippets, absolute http(s) URLs only, no duplicates, at most ``limit``."""
    out: list[SearchResult] = []
    seen: set[str] = set()
    for r in raw:
        url = normalize_url(r.url or "") if (r.url or "").lower().startswith(("http://", "https://")) else None
        title = html_to_text(r.title)
        if not url or not title or url in seen:
            continue
        seen.add(url)
        snippet = html_to_text(r.snippet)
        out.append(SearchResult(title, url, snippet[:SNIPPET_CHARS], r.published))
        if len(out) >= limit:
            break
    return out
