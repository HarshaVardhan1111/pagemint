"""Ashby public job posting API (keyless)."""

from __future__ import annotations

from urllib.parse import quote

from pagemint.jobs.model import Board, Job, parse_date
from pagemint.jobs.readers._common import get_json

BASE = "https://api.ashbyhq.com"
SOURCE = "ashby"
_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


def _remote(raw: dict) -> bool | None:
    # Ashby sets isRemote on hybrid jobs too, so the workplace type wins when present.
    wp = _WORKPLACE.get((raw.get("workplaceType") or "").lower())
    if wp == "remote":
        return True
    if wp in ("hybrid", "onsite"):
        return False
    return raw.get("isRemote") if isinstance(raw.get("isRemote"), bool) else None


def to_job(raw: dict, board: str) -> Job:
    location = raw.get("location") or ""
    secondary = []
    for s in raw.get("secondaryLocations") or []:
        name = s.get("location") if isinstance(s, dict) else s
        if isinstance(name, str) and name:
            secondary.append(name)
    comp = raw.get("compensation") or {}
    summary = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary")
    return Job(
        title=(raw.get("title") or "").strip(),
        company=board,
        url=raw.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{raw.get('id')}",
        source=SOURCE,
        location=location,
        locations=([location] if location else []) + secondary,
        remote=_remote(raw),
        workplace_type=_WORKPLACE.get((raw.get("workplaceType") or "").lower()),
        employment_type=raw.get("employmentType"),
        department=raw.get("department") or raw.get("team"),
        posted_at=parse_date(raw.get("publishedAt")),
        salary={"min": None, "max": None, "currency": None, "period": None, "text": summary} if summary else None,
        apply_url=raw.get("applyUrl"),
        description=raw.get("descriptionHtml") or raw.get("descriptionPlain") or "",
        job_id=raw.get("id"),
    )


async def _raw_jobs(client, board: Board) -> list[dict]:
    url = f"{BASE}/posting-api/job-board/{quote(board.board, safe='')}?includeCompensation=true"
    data = await get_json(client, url, f"Ashby job board '{board.board}'")
    raws = data.get("jobs") if isinstance(data, dict) else None
    return [r for r in raws or [] if isinstance(r, dict)]


async def list_jobs(client, board: Board) -> tuple[list[Job], str | None]:
    raws = await _raw_jobs(client, board)
    return [to_job(r, board.board) for r in raws if r.get("isListed", True) and r.get("title")], None


async def get_job(client, board: Board, job_id: str) -> Job | None:
    # Ashby has no public single-job endpoint: an open job is one still listed on the board.
    for r in await _raw_jobs(client, board):
        if str(r.get("id")) == str(job_id):
            return to_job(r, board.board) if r.get("isListed", True) else None
    return None
