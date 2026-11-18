"""Main-content extraction: strip boilerplate, then pick the region holding the content.

Approach (written for PageMint):
1. Drop non-content tags and hidden elements.
2. Drop boilerplate (nav, page header/footer, asides, cookie banners, ads...),
   unless an element holds most of the page's text.
3. Pick the main region: <main>/[role=main] or a single <article> when they
   hold enough of the text; otherwise the deepest container that still holds
   most of the page's non-link text. Fallback: <body>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree
from lxml import html as lxml_html

_DROP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "canvas", "template", "object", "embed",
    "input", "select", "textarea", "button", "option", "link", "meta", "dialog",
}
_BOILERPLATE_TAGS = {"nav", "aside"}
_PAGE_CHROME_TAGS = {"header", "footer"}  # only outside <main>/<article>
_BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alertdialog"}
_BOILERPLATE_WORDS = {
    "cookie", "cookies", "consent", "gdpr", "banner", "advert", "advertisement", "ad", "ads", "adsbygoogle",
    "sponsor", "sponsored", "promo", "promotion", "newsletter", "subscribe", "subscription", "share",
    "sharing", "social", "sidebar", "breadcrumb", "breadcrumbs", "popup", "modal", "overlay", "related",
    "comments", "comment", "skip", "menu", "navbar", "toolbar", "masthead", "signup",
}
_CONTAINER_TAGS = {"div", "section", "article", "main", "td", "body", "table", "tbody", "ul", "ol", "dl", "form"}
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_HIDDEN_STYLE_RE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.I)
_WS_RE = re.compile(r"\s+")

# Share of the page's non-link text a region must hold to be chosen.
MAIN_MIN_SHARE = 0.5
DEEPEST_MIN_SHARE = 0.75
# An element holding more than this share of text is never removed as boilerplate.
KEEP_IF_SHARE = 0.6


@dataclass
class Extracted:
    title: str | None
    main_html: str
    text_len: int
    doc: object | None = None  # the parsed (uncleaned) document, for link extraction


def parse_html(html: str):
    if not html or not html.strip():
        return None
    try:
        return lxml_html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return None


def _clean_text(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


def _title(doc) -> str | None:
    for xp in ('//meta[@property="og:title"]/@content', '//meta[@name="twitter:title"]/@content', "//title/text()", "//h1"):
        for v in doc.xpath(xp):
            t = _clean_text(v.text_content() if hasattr(v, "text_content") else str(v))
            if t:
                return t
    return None


def _tokens(el) -> set[str]:
    raw = f"{el.get('id') or ''} {el.get('class') or ''}".lower()
    return {t for t in _TOKEN_RE.split(raw) if t}


def _text_len_map(root) -> dict:
    """Non-link text length for every element, computed in one bottom-up pass."""
    sizes: dict = {}

    def walk(el, in_link: bool) -> int:
        here_link = in_link or el.tag == "a"
        n = 0 if here_link else len((el.text or "").strip())
        for child in el:
            if isinstance(child.tag, str):
                n += walk(child, here_link)
            if not here_link and child.tail:
                n += len(child.tail.strip())
        sizes[el] = n
        return n

    walk(root, False)
    return sizes


def _remove(el) -> None:
    """Remove an element but keep its tail text attached to the document."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _is_hidden(el) -> bool:
    if el.get("hidden") is not None:
        return True
    if (el.get("aria-hidden") or "").lower() == "true":
        return True
    style = el.get("style")
    return bool(style and _HIDDEN_STYLE_RE.search(style))


def _inside_content(el) -> bool:
    return any(a.tag in ("main", "article") or (a.get("role") or "").lower() == "main" for a in el.iterancestors())


def _strip(body) -> None:
    # 1. Non-content tags, comments and hidden elements.
    for el in list(body.iter()):
        if not isinstance(el.tag, str):
            if el.getparent() is not None:
                _remove(el)
            continue
        if el.tag in _DROP_TAGS or _is_hidden(el):
            _remove(el)

    # 2. Boilerplate, guarded so a wrapper holding most of the text survives.
    sizes = _text_len_map(body)
    total = max(sizes.get(body, 0), 1)
    for el in list(body.iter()):
        if not isinstance(el.tag, str) or el is body or el.getparent() is None:
            continue
        # Skip elements already detached with a removed ancestor.
        if not any(a is body for a in el.iterancestors()):
            continue
        role = (el.get("role") or "").lower()
        is_boiler = (
            el.tag in _BOILERPLATE_TAGS
            or role in _BOILERPLATE_ROLES
            or (el.tag in _PAGE_CHROME_TAGS and not _inside_content(el))
            or bool(_tokens(el) & _BOILERPLATE_WORDS)
        )
        if not is_boiler:
            continue
        if el.tag not in _BOILERPLATE_TAGS and sizes.get(el, 0) / total > KEEP_IF_SHARE:
            continue
        _remove(el)


def _pick_main(body):
    sizes = _text_len_map(body)
    total = sizes.get(body, 0)
    if total <= 0:
        return body

    mains = [el for el in body.iter("main")] + body.xpath('.//*[@role="main"]')
    for m in mains:
        if sizes.get(m, 0) / total >= MAIN_MIN_SHARE:
            return m

    articles = list(body.iter("article"))
    if len(articles) == 1 and sizes.get(articles[0], 0) / total >= MAIN_MIN_SHARE:
        return articles[0]

    # Deepest container that still holds most of the non-link text. Elements
    # holding more than half the text form a single chain, so walk down it.
    best = body
    while True:
        nxt = None
        for child in best:
            if isinstance(child.tag, str) and child.tag in _CONTAINER_TAGS and sizes.get(child, 0) / total >= DEEPEST_MIN_SHARE:
                nxt = child
                break
        if nxt is None:
            return best
        best = nxt


def extract_main(html: str, url: str, doc=None) -> Extracted:
    """Find the title and main-content HTML of a page."""
    doc = doc if doc is not None else parse_html(html)
    if doc is None:
        return Extracted(title=None, main_html="", text_len=0, doc=None)
    title = _title(doc)

    work = lxml_html.fromstring(lxml_html.tostring(doc))  # cleaned copy; keep doc intact for links
    body = work.find(".//body")
    if body is None:
        body = work
    _strip(body)
    main = _pick_main(body)
    main_html = lxml_html.tostring(main, encoding="unicode", method="html")
    text_len = len(_clean_text(main.text_content()))
    return Extracted(title=title, main_html=main_html, text_len=text_len, doc=doc)
