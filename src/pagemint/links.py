"""URL normalization and link extraction."""

from __future__ import annotations

import posixpath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical absolute http(s) URL, or None if it is not http(s).

    Resolves against ``base``, lowercases scheme and host, drops default ports
    and the fragment, resolves ``.``/``..`` segments, and makes the path "/"
    when empty.
    """
    if not url:
        return None
    url = url.strip()
    try:
        if base:
            url = urljoin(base, url)
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORTS or not parts.hostname:
            return None
        host = parts.hostname.lower()
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        port = parts.port
    except ValueError:
        return None
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parts.path or "/"
    if "." in path:
        trailing = path.endswith("/")
        path = posixpath.normpath(path)
        if path.startswith("//"):
            path = "/" + path.lstrip("/")
        if trailing and not path.endswith("/"):
            path += "/"
    path = quote(unquote(path), safe="/%:@!$&'()*+,;=-._~")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def same_site(a: str, b: str) -> bool:
    """Same host, treating a leading ``www.`` as the same site."""

    def strip(h: str) -> str:
        return h[4:] if h.startswith("www.") else h

    return strip(host_of(a)) == strip(host_of(b))


def base_url_of(doc, page_url: str) -> str:
    bases = doc.xpath("//base[@href]/@href") if doc is not None else []
    return urljoin(page_url, bases[0].strip()) if bases else page_url


def extract_links(html: str, base_url: str) -> list[str]:
    """All http(s) links in the page, absolute, normalized, de-duplicated, in page order."""
    if not html or not html.strip():
        return []
    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return []
    return links_from_doc(doc, base_url)


def links_from_doc(doc, page_url: str) -> list[str]:
    base = base_url_of(doc, page_url)
    seen: dict[str, None] = {}
    for href in doc.xpath("//a[@href]/@href"):
        u = normalize_url(str(href), base)
        if u and u not in seen:
            seen[u] = None
    return list(seen)
