"""HTML fragment to Markdown, built on markdownify with PageMint's tweaks."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from markdownify import ATX, MarkdownConverter

_LANG_RE = re.compile(r"^(?:language|lang|highlight-source)-([\w+#.-]+)$")
_BLANKS_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")


def _code_language(el) -> str:
    """Language from ``class="language-x"`` on the <pre> or its <code> child."""
    nodes = [el]
    code = el.find("code")
    if code is not None:
        nodes.append(code)
    for node in nodes:
        for cls in node.get("class") or []:
            m = _LANG_RE.match(cls)
            if m:
                return m.group(1)
    return ""


class _Converter(MarkdownConverter):
    def __init__(self, base_url: str, **options):
        super().__init__(**options)
        self.base_url = base_url

    def _abs(self, url: str) -> str:
        url = (url or "").strip()
        if not url or url.startswith(("data:", "javascript:")):
            return url
        try:
            return urljoin(self.base_url, url)
        except ValueError:
            return url

    def convert_a(self, el, text, parent_tags):
        if not (text or "").strip():
            return ""  # empty links (icons, anchors) add only noise
        href = el.get("href")
        if href:
            href = self._abs(href)
            if href.startswith("javascript:"):
                return text
            el["href"] = href
        return super().convert_a(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags):
        src = el.get("src")
        if not src or src.startswith("data:"):
            return ""  # inline data images bloat the output
        el["src"] = self._abs(src)
        return super().convert_img(el, text, parent_tags)


def to_markdown(html: str, base_url: str) -> str:
    if not html or not html.strip():
        return ""
    conv = _Converter(
        base_url,
        heading_style=ATX,
        bullets="-",
        code_language_callback=_code_language,
        escape_underscores=False,
        escape_asterisks=False,
        table_infer_header=True,
        bs4_options="lxml",
    )
    md = conv.convert(html)
    md = _TRAILING_WS_RE.sub("\n", md)
    md = _BLANKS_RE.sub("\n\n", md)
    return md.strip() + "\n" if md.strip() else ""
