"""Recruitee careers-site API (experimental: built from Recruitee's documentation)."""

from __future__ import annotations

from urllib.parse import quote

from pagemint.jobs.model import Board, Job, parse_date
from pagemint.jobs.readers._common import get_json, get_json_or_none

HOST_OVERRIDE: str | None = None  # tests point this at a local server
SOURCE = "recruitee"


def _root(board: Board) -> str:
    return HOST_OVERRIDE or f"https://{board.host or board.board + '.recruitee.com'}"


def to_job(raw: dict, board: Board) -> Job:
    location = raw.get("location") or ", ".join(x for x in (raw.get("city"), raw.get("country")) if x)
    desc = (raw.get("description") or "") + (raw.get("requirements") or "")
    return Job(
        title=(raw.get("title") or "").strip(), company=board.board,
        url=raw.get("careers_url") or f"{_root(board)}/o/{raw.get('slug')}", source=SOURCE,
        location=location, locations=[location] if location else [],
        remote=raw.get("remote") if isinstance(raw.get("remote"), bool) else None,
        employment_type=raw.get("employment_type_code"), department=raw.get("department"),
        posted_at=parse_date(raw.get("published_at")), apply_url=raw.get("careers_apply_url"),
        description=desc, job_id=raw.get("slug") or (str(raw["id"]) if raw.get("id") is not None else None),
    )


async def list_jobs(client, board: Board) -> tuple[list[Job], str | None]:
    data = await get_json(client, f"{_root(board)}/api/offers/", f"Recruitee site '{board.board}'")
    raws = data.get("offers") if isinstance(data, dict) else None
    return [to_job(r, board) for r in raws or [] if isinstance(r, dict) and r.get("title")], None


async def get_job(client, board: Board, slug: str) -> Job | None:
    data = await get_json_or_none(client, f"{_root(board)}/api/offers/{quote(slug, safe='')}", f"Recruitee offer {slug}")
    raw = data.get("offer") if isinstance(data, dict) else None
    return to_job(raw, board) if isinstance(raw, dict) and raw.get("title") else None
