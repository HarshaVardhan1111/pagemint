"""The common Job shape every source is converted into."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SNIPPET_CHARS = 300
FULL_CHARS = 20_000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def html_to_text(value: str | None) -> str:
    """Plain text from HTML, including HTML that was escaped once more (Greenhouse)."""
    if not value:
        return ""
    text = value
    for _ in range(2):  # undo up to two levels of escaping
        if "&lt;" in text or "&amp;" in text or "&#" in text:
            text = html_lib.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def parse_date(value) -> str | None:
    """ISO date (YYYY-MM-DD) from an ISO string, unix seconds or milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    m = _ISO_DATE_RE.match(str(value).strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError:
        return None


@dataclass
class Board:
    """Where a company's jobs live."""

    system: str  # greenhouse | lever | ashby | workday | recruitee | workable | jsonld
    board: str  # board token / site / tenant / careers URL for jsonld
    host: str | None = None  # Lever API host, Workday host, Recruitee host
    site: str | None = None  # Workday site name
    careers_url: str | None = None


@dataclass
class Job:
    title: str
    company: str
    url: str
    source: str
    location: str = ""
    locations: list[str] = field(default_factory=list)
    remote: bool | None = None
    workplace_type: str | None = None  # remote | hybrid | onsite
    employment_type: str | None = None
    department: str | None = None
    posted_at: str | None = None
    valid_through: str | None = None
    salary: dict | None = None
    apply_url: str | None = None
    description: str = ""  # HTML or plain text as received
    status: str = "open"  # open | unverified
    job_id: str | None = None

    def text(self) -> str:
        return html_to_text(self.description)

    def to_dict(self, snippet: bool = True) -> dict:
        d = asdict(self)
        d.pop("job_id", None)
        if snippet:
            t = self.text()
            d["description"] = t if len(t) <= SNIPPET_CHARS else t[: SNIPPET_CHARS - 1].rstrip() + "…"
        else:
            from pagemint.markdown import to_markdown

            body = self.description or ""
            md = to_markdown(body, self.url) if "<" in body else body
            d["description"] = md[:FULL_CHARS]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
