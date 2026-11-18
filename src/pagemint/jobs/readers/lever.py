"""Lever Postings API (public, keyless; one site per company)."""

from __future__ import annotations

from urllib.parse import quote

from pagemint.jobs.model import Board, Job, parse_date
from pagemint.jobs.readers._common import get_json, get_json_or_none

GLOBAL_HOST = "api.lever.co"
EU_HOST = "api.eu.lever.co"
HOST_OVERRIDE: str | None = None  # tests point this at a local server
SOURCE = "lever"
_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


def _base(board: Board) -> str:
    return HOST_OVERRIDE or f"https://{board.host or GLOBAL_HOST}"


def _salary(raw: dict) -> dict | None:
    s = raw.get("salaryRange")
    if not isinstance(s, dict):
        return None
    interval = (s.get("interval") or "").lower()
    period = next((p for p in ("year", "month", "week", "day", "hour") if p in interval), None)
    return {"min": s.get("min"), "max": s.get("max"), "currency": s.get("currency"), "period": period,
            "text": raw.get("salaryDescriptionPlain")}


def to_job(raw: dict, site: str) -> Job:
    cats = raw.get("categories") or {}
    wp = _WORKPLACE.get((raw.get("workplaceType") or "").lower())
    location = cats.get("location") or ""
    return Job(
        title=(raw.get("text") or "").strip(),
        company=site,
        url=raw.get("hostedUrl") or f"https://jobs.lever.co/{site}/{raw.get('id')}",
        source=SOURCE,
        location=location,
        locations=list(cats.get("allLocations") or ([location] if location else [])),
        remote=True if wp == "remote" else (False if wp in ("hybrid", "onsite") else None),
        workplace_type=wp,
        employment_type=cats.get("commitment"),
        department=cats.get("department") or cats.get("team"),
        posted_at=parse_date(raw.get("createdAt")),
        salary=_salary(raw),
        apply_url=raw.get("applyUrl"),
        description=raw.get("description") or raw.get("descriptionPlain") or "",
        job_id=raw.get("id"),
    )


async def list_jobs(client, board: Board) -> tuple[list[Job], str | None]:
    site = quote(board.board, safe="")
    data = await get_json(client, f"{_base(board)}/v0/postings/{site}?mode=json", f"Lever site '{board.board}'")
    raws = data if isinstance(data, list) else []
    return [to_job(r, board.board) for r in raws if isinstance(r, dict) and r.get("text")], None


async def get_job(client, board: Board, job_id: str) -> Job | None:
    url = f"{_base(board)}/v0/postings/{quote(board.board, safe='')}/{quote(job_id, safe='')}?mode=json"
    data = await get_json_or_none(client, url, f"Lever posting {job_id}")
    return to_job(data, board.board) if isinstance(data, dict) and data.get("text") else None
