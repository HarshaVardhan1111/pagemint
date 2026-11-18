"""Arbeitnow free job board API (keyless; Europe-heavy). Terms ask for a link back."""

from __future__ import annotations

from pagemint.jobs.model import Job, parse_date
from pagemint.jobs.readers._common import get_json

BASE = "https://www.arbeitnow.com"
SOURCE = "arbeitnow"
CREDIT = "Arbeitnow (https://www.arbeitnow.com)"
MAX_PAGES = 3


def to_job(raw: dict) -> Job:
    types = raw.get("job_types") or []
    return Job(
        title=(raw.get("title") or "").strip(), company=raw.get("company_name") or "",
        url=raw.get("url") or f"{BASE}/jobs/{raw.get('slug')}", source=SOURCE,
        location=raw.get("location") or "", locations=[raw["location"]] if raw.get("location") else [],
        remote=raw.get("remote") if isinstance(raw.get("remote"), bool) else None,
        employment_type=", ".join(types) if types else None, posted_at=parse_date(raw.get("created_at")),
        apply_url=raw.get("url"), description=raw.get("description") or "", status="unverified",
        job_id=raw.get("slug"),
    )


async def fetch(client, query: str) -> list[Job]:
    jobs: list[Job] = []
    for page in range(1, MAX_PAGES + 1):
        data = await get_json(client, f"{BASE}/api/job-board-api?page={page}", "Arbeitnow job board")
        jobs.extend(to_job(r) for r in data.get("data") or [] if isinstance(r, dict) and r.get("title"))
        if not (data.get("links") or {}).get("next"):
            break
    return jobs
