"""General reader: schema.org JobPosting data that job pages embed for search engines."""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import time
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from lxml import html as lxml_html

from pagemint.errors import PageMintError
from pagemint.jobs.model import Job, parse_date
from pagemint.links import links_from_doc, normalize_url, same_site

if TYPE_CHECKING:
    from pagemint.client import PageMint

log = logging.getLogger("pagemint.jobs")

SOURCE = "jsonld"
MAX_JOB_PAGES = 30
_JOB_LINK_RE = re.compile(
    r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|requisitions?|join-us|work-with-us)/[^/?#]+", re.I
)
_PERIODS = {"YEAR": "year", "MONTH": "month", "WEEK": "week", "DAY": "day", "HOUR": "hour"}


def _types(obj: dict) -> set[str]:
    t = obj.get("@type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {x for x in t if isinstance(x, str)}
    return set()


def _walk(node):
    """Yield every dict in a JSON-LD structure (handles @graph and lists)."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _walk(node["@graph"])


def _text(value) -> str:
    if isinstance(value, str):
        return html_lib.unescape(value).strip()
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@value") or "")
    return ""


def _location(posting: dict) -> list[str]:
    raw = posting.get("jobLocation")
    places = raw if isinstance(raw, list) else [raw] if raw else []
    out = []
    for place in places:
        if not isinstance(place, dict):
            continue
        addr = place.get("address")
        if isinstance(addr, str):
            out.append(addr.strip())
            continue
        if isinstance(addr, dict):
            parts = [_text(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
            joined = ", ".join(p for p in parts if p)
            if joined:
                out.append(joined)
    return out


def _number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _salary(posting: dict) -> dict | None:
    s = posting.get("baseSalary") or posting.get("estimatedSalary")
    if isinstance(s, list):
        s = s[0] if s else None
    if not isinstance(s, dict):
        return None
    value = s.get("value")
    unit = s.get("unitText")
    if isinstance(value, dict):
        lo = _number(value.get("minValue"))
        hi = _number(value.get("maxValue"))
        if lo is None and hi is None:
            lo = hi = _number(value.get("value"))
        unit = value.get("unitText") or unit
    else:
        lo = hi = _number(value)
    if lo is None and hi is None:
        return None
    return {"min": lo, "max": hi, "currency": s.get("currency"),
            "period": _PERIODS.get(str(unit or "").upper()), "text": None}


def _to_job(posting: dict, page_url: str) -> Job | None:
    title = _text(posting.get("title"))
    if not title:
        return None
    locs = _location(posting)
    remote = True if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None
    emp = posting.get("employmentType")
    if isinstance(emp, list):
        emp = ", ".join(str(e) for e in emp)
    url = posting.get("url") if isinstance(posting.get("url"), str) else None
    url = normalize_url(url, page_url) if url else None
    return Job(
        title=title,
        company=_text(posting.get("hiringOrganization")),
        url=url or page_url,
        source=SOURCE,
        location=" | ".join(locs) if len(locs) > 1 else (locs[0] if locs else ("Remote" if remote else "")),
        locations=locs,
        remote=remote,
        employment_type=emp if isinstance(emp, str) else None,
        posted_at=parse_date(posting.get("datePosted")),
        valid_through=parse_date(posting.get("validThrough")),
        salary=_salary(posting),
        apply_url=url or page_url,
        description=posting.get("description") if isinstance(posting.get("description"), str) else "",
        job_id=str(posting.get("identifier", {}).get("value")) if isinstance(posting.get("identifier"), dict)
        and posting["identifier"].get("value") is not None else None,
    )


def parse_jobpostings(html: str, page_url: str, today: date | None = None) -> list[Job]:
    """All JobPosting entries in a page's JSON-LD; expired ones are dropped."""
    if not html or "ld+json" not in html:
        return []
    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return []
    today_s = (today or date.today()).isoformat()
    jobs: list[Job] = []
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        raw = (script.text or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue  # malformed blocks are common; skip them
        for node in _walk(data):
            if "JobPosting" not in _types(node):
                continue
            job = _to_job(node, page_url)
            if job is None:
                continue
            if job.valid_through and job.valid_through < today_s:
                continue
            jobs.append(job)
    return jobs


def _job_links(html: str, page_url: str) -> list[str]:
    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return []
    page = normalize_url(page_url)
    out = []
    for link in links_from_doc(doc, page_url):
        if link == page or not same_site(link, page_url):
            continue
        if _JOB_LINK_RE.search(urlsplit(link).path):
            out.append(link)
    return out


async def read_careers_page(client: "PageMint", url: str, deadline: float) -> list[Job]:
    """Jobs from a careers page: its own JobPosting data, else up to 30 linked job pages."""
    res = await client.fetcher.fetch(url)
    jobs = parse_jobpostings(res.text, res.url)
    if jobs:
        return jobs
    for link in _job_links(res.text, res.url)[:MAX_JOB_PAGES]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            page = await asyncio.wait_for(client.fetcher.fetch(link), timeout=remaining)
        except (PageMintError, asyncio.TimeoutError) as e:
            log.debug("skipping job page %s: %s", link, e)
            continue
        jobs.extend(parse_jobpostings(page.text, page.url))
    return jobs
