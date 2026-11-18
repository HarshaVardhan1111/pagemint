"""sitemap.xml parsing (urlset and sitemapindex, optionally gzipped)."""

from __future__ import annotations

import zlib

from lxml import etree

# Decompressed sitemaps are capped (the protocol allows 50 MB uncompressed).
MAX_SITEMAP_BYTES = 50 * 1024 * 1024

_PARSER_KW = dict(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, MAX_SITEMAP_BYTES)
    return out


def parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Return ``(page_urls, child_sitemap_urls)``. Bad input returns two empty lists."""
    if not content:
        return [], []
    try:
        if content[:2] == b"\x1f\x8b":
            content = _gunzip(content)
        root = etree.fromstring(content.lstrip(), etree.XMLParser(**_PARSER_KW))
    except (etree.XMLSyntaxError, ValueError, zlib.error):
        return [], []
    if root is None:
        return [], []
    tag = etree.QName(root).localname if isinstance(root.tag, str) else ""
    locs = [
        (el.text or "").strip()
        for el in root.iter()
        if isinstance(el.tag, str) and etree.QName(el).localname == "loc"
    ]
    locs = [u for u in locs if u]
    if tag == "sitemapindex":
        return [], locs
    if tag == "urlset":
        return locs, []
    return [], []
