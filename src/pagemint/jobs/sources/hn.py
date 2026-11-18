"""Hacker News "Ask HN: Who is hiring?" threads, via the public HN Algolia API (keyless)."""

from __future__ import annotations

import html as html_lib
import re
from datetime import date, timedelta

from pagemint.jobs.model import Job, html_to_text, parse_date
from pagemint.jobs.readers._common import get_json

BASE = "https://hn.algolia.com"
SOURCE = "hn"
CREDIT = "Hacker News “Who is hiring?” (news.ycombinator.com)"
MAX_AGE_DAYS = 45
THREADS = 2  # current and previous month

_ROLE_RE = re.compile(
    r"\b(engineers?|developers?|managers?|designers?|scientists?|analysts?|leads?|interns?|internship|architects?|"
    r"directors?|head|researchers?|sre|devops|consultants?|specialists?|administrators?|recruiters?|writers?|"
    r"marketers?|marketing|sales|product|founding|cto|vp|officers?|technicians?|programmers?|coordinators?|"
    r"associates?|accountants?|operations|support|qa|tester|swe|ml|ai)\b",
    re.I,
)
_EMPLOYMENT_RE = re.compile(r"^\s*(full[- ]?time|part[- ]?time|contract(or)?|intern(ship)?|freelance|temporary)\b", re.I)
_SALARY_RE = re.compile(r"[$€£₹¥]|\b\d{2,3}\s?k\b|\b\d{1,3}(,\d{3})+\b", re.I)
_URLISH_RE = re.compile(r"^(https?://|www\.)", re.I)
_TAGS_ONLY_RE = re.compile(r"^\s*(onsite|on-site|hybrid|visa( sponsorship)?|equity|remote ok)\s*$", re.I)
_HREF_RE = re.compile(r'href="([^"]+)"')
_JOBBY_RE = re.compile(
    r"greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|myworkdayjobs\.com|recruitee\.com|smartrecruiters\.com|"
    r"/(?:jobs?|careers?|apply|positions?|openings?|join|hiring|work-with-us)\b",
    re.I,
)
_NOT_JOB_RE = re.compile(r"youtube\.com|youtu\.be|twitter\.com|x\.com/|linkedin\.com/posts|github\.com", re.I)


def _best_link(text_html: str) -> str | None:
    links = [html_lib.unescape(m) for m in _HREF_RE.findall(text_html or "")]
    for link in links:
        if _JOBBY_RE.search(link):
            return link
    for link in links:
        if not _NOT_JOB_RE.search(link):
            return link
    return links[0] if links else None


def parse_post(text_html: str, item_id: int | str, created_at: str | None) -> Job:
    first_html = re.split(r"<p>", text_html or "", maxsplit=1, flags=re.I)[0]
    first = html_to_text(first_html)
    fields = [f.strip() for f in first.split("|") if f.strip()]
    url = _best_link(text_html) or f"https://news.ycombinator.com/item?id={item_id}"

    company, title, location, employment, salary = "", "", "", None, None
    remote = True if re.search(r"\bremote\b", first, re.I) else None
    if len(fields) >= 2:
        company = fields[0]
        rest = fields[1:]
        for f in rest:
            if not title and _ROLE_RE.search(f) and not _SALARY_RE.search(f):
                title = f
        for f in rest:
            if f == title:
                continue
            if employment is None and _EMPLOYMENT_RE.match(f):
                employment = f
            elif salary is None and _SALARY_RE.search(f):
                salary = f
            elif not location and not _URLISH_RE.match(f) and not _TAGS_ONLY_RE.match(f):
                location = f
    if not title:
        title = first[:120].rstrip()
    return Job(
        title=title, company=company, url=url, source=SOURCE, location=location,
        locations=[location] if location else [], remote=remote, employment_type=employment,
        posted_at=parse_date(created_at),
        salary={"min": None, "max": None, "currency": None, "period": None, "text": salary} if salary else None,
        apply_url=url, description=text_html or "", status="unverified", job_id=str(item_id),
    )


async def fetch(client, query: str) -> list[Job]:
    data = await get_json(client, f"{BASE}/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10",
                          "Hacker News search")
    hits = [h for h in (data.get("hits") or []) if str(h.get("title", "")).startswith("Ask HN: Who is hiring?")]
    cutoff = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    jobs: list[Job] = []
    for hit in hits[:THREADS]:
        item = await get_json(client, f"{BASE}/api/v1/items/{hit['objectID']}", "Hacker News thread")
        for child in item.get("children") or []:
            if not isinstance(child, dict) or not child.get("text"):
                continue
            posted = parse_date(child.get("created_at"))
            if posted and posted < cutoff:
                continue
            jobs.append(parse_post(child["text"], child.get("id"), child.get("created_at")))
    return jobs
