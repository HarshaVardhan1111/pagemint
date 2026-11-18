"""Greenhouse Job Board API (public, keyless)."""

from __future__ import annotations

from urllib.parse import quote

from pagemint.jobs.model import Board, Job, parse_date
from pagemint.jobs.readers._common import get_json, get_json_or_none, remote_from_text

BASE = "https://boards-api.greenhouse.io"
SOURCE = "greenhouse"


def _salary(raw: dict) -> dict | None:
    ranges = raw.get("pay_input_ranges") or []
    if not ranges:
        return None
    r = ranges[0]
    cents = lambda v: round(v / 100, 2) if isinstance(v, (int, float)) else None  # noqa: E731
    return {"min": cents(r.get("min_cents")), "max": cents(r.get("max_cents")),
            "currency": r.get("currency_type"), "period": None, "text": r.get("title")}


def to_job(raw: dict, token: str) -> Job:
    location = ((raw.get("location") or {}).get("name") or "").strip()
    depts = raw.get("departments") or []
    return Job(
        title=(raw.get("title") or "").strip(),
        company=raw.get("company_name") or token,
        url=raw.get("absolute_url") or f"https://boards.greenhouse.io/{token}/jobs/{raw.get('id')}",
        source=SOURCE,
        location=location,
        locations=[location] if location else [],
        remote=remote_from_text(location),
        department=(depts[0].get("name") if depts and isinstance(depts[0], dict) else None),
        posted_at=parse_date(raw.get("first_published")) or parse_date(raw.get("updated_at")),
        valid_through=parse_date(raw.get("application_deadline")),
        salary=_salary(raw),
        apply_url=raw.get("absolute_url"),
        description=raw.get("content") or "",
        job_id=str(raw.get("id")) if raw.get("id") is not None else None,
    )


async def list_jobs(client, board: Board) -> tuple[list[Job], str | None]:
    token = quote(board.board, safe="")
    data = await get_json(client, f"{BASE}/v1/boards/{token}/jobs?content=true", f"Greenhouse board '{board.board}'")
    raws = data.get("jobs") or [] if isinstance(data, dict) else []
    jobs = [to_job(r, board.board) for r in raws if isinstance(r, dict) and r.get("title")]
    name = next((r.get("company_name") for r in raws if isinstance(r, dict) and r.get("company_name")), None)
    return jobs, name


async def get_job(client, board: Board, job_id: str) -> Job | None:
    url = f"{BASE}/v1/boards/{quote(board.board, safe='')}/jobs/{quote(str(job_id), safe='')}?pay_transparency=true"
    data = await get_json_or_none(client, url, f"Greenhouse job {job_id}")
    return to_job(data, board.board) if isinstance(data, dict) else None
