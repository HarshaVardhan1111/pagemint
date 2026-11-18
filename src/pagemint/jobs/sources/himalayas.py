"""Himalayas remote jobs API (keyless; rate-limits quickly, so best effort)."""

from __future__ import annotations

import time
from urllib.parse import quote

from pagemint.errors import FetchError
from pagemint.jobs.model import Job, parse_date

BASE = "https://himalayas.app"
SOURCE = "himalayas"
CREDIT = "Himalayas (https://himalayas.app)"
_PERIODS = {"hourly": "hour", "hour": "hour", "daily": "day", "weekly": "week", "monthly": "month",
            "annual": "year", "annually": "year", "yearly": "year", "year": "year"}


def to_job(raw: dict) -> Job:
    locs = [x for x in raw.get("locationRestrictions") or [] if isinstance(x, str)]
    cats = raw.get("parentCategories") or raw.get("categories") or []
    lo, hi = raw.get("minSalary"), raw.get("maxSalary")
    url = raw.get("applicationLink") or raw.get("guid") or ""
    return Job(
        title=(raw.get("title") or "").strip(), company=raw.get("companyName") or "", url=url, source=SOURCE,
        location=", ".join(locs) if locs else "Remote", locations=locs, remote=True, workplace_type="remote",
        employment_type=raw.get("employmentType"), department=cats[0] if cats else None,
        posted_at=parse_date(raw.get("pubDate")), valid_through=parse_date(raw.get("expiryDate")),
        salary={"min": lo, "max": hi, "currency": raw.get("currency"),
                "period": _PERIODS.get(str(raw.get("salaryPeriod") or "").lower()), "text": None}
        if lo is not None or hi is not None else None,
        apply_url=url, description=raw.get("description") or raw.get("excerpt") or "", status="unverified",
        job_id=raw.get("guid"),
    )


async def fetch(client, query: str) -> list[Job]:
    url = f"{BASE}/jobs/api/search?q={quote(query)}" if query.strip() else f"{BASE}/jobs/api?limit=20"
    status, data = await client.fetcher.fetch_json(url)
    if status == 429:
        raise FetchError("Himalayas is rate limiting requests (HTTP 429); its jobs are left out this time.")
    if not 200 <= status < 300 or not isinstance(data, dict):
        raise FetchError(f"Himalayas could not be read (HTTP {status}).")
    now = time.time()
    jobs = []
    for raw in data.get("jobs") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        exp = raw.get("expiryDate")
        if isinstance(exp, (int, float)) and exp < now:
            continue
        jobs.append(to_job(raw))
    return jobs
