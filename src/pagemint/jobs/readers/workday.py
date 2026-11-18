"""Workday career sites (the JSON endpoints the career site itself uses)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote

from pagemint.jobs.model import Board, Job, parse_date
from pagemint.jobs.readers._common import get_json, get_json_or_none, remote_from_text

HOST_OVERRIDE: str | None = None  # tests point this at a local server
SOURCE = "workday"
PAGE_SIZE = 20
MAX_JOBS = 200
_AGO_RE = re.compile(r"(\d+)\s+days?\s+ago", re.I)


def _api(board: Board) -> str:
    root = HOST_OVERRIDE or f"https://{board.host}"
    return f"{root}/wday/cxs/{quote(board.board, safe='')}/{quote(board.site or '', safe='')}"


def posted_on_to_date(text: str | None, today: date | None = None) -> str | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 5 Days Ago'. '30+ Days Ago' is unknown."""
    if not text:
        return None
    today = today or date.today()
    t = text.lower()
    if "+" in t:
        return None
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = _AGO_RE.search(t)
    return (today - timedelta(days=int(m.group(1)))).isoformat() if m else None


def _public_url(board: Board, external_path: str) -> str:
    return f"https://{board.host}/{board.site}{external_path}"


async def list_jobs(client, board: Board) -> tuple[list[Job], str | None]:
    jobs: list[Job] = []
    offset = 0
    what = f"Workday site '{board.host}/{board.site}'"
    while len(jobs) < MAX_JOBS:
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        data = await get_json(client, f"{_api(board)}/jobs", what, method="POST", body=body)
        posts = data.get("jobPostings") if isinstance(data, dict) else None
        if not posts:
            break
        for p in posts:
            if not isinstance(p, dict) or not p.get("title") or not p.get("externalPath"):
                continue
            loc = p.get("locationsText") or ""
            jobs.append(Job(
                title=p["title"].strip(), company=board.board, url=_public_url(board, p["externalPath"]),
                source=SOURCE, location=loc, locations=[loc] if loc else [], remote=remote_from_text(loc),
                posted_at=posted_on_to_date(p.get("postedOn")), apply_url=_public_url(board, p["externalPath"]),
                job_id=p["externalPath"],
            ))
        offset += PAGE_SIZE
        total = data.get("total")
        if isinstance(total, int) and offset >= total:
            break
    return jobs[:MAX_JOBS], None


async def get_job(client, board: Board, external_path: str) -> Job | None:
    data = await get_json_or_none(client, f"{_api(board)}{external_path}", f"Workday job {external_path}")
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not info.get("title"):
        return None
    loc = info.get("location") or ""
    return Job(
        title=info["title"].strip(), company=board.board,
        url=info.get("externalUrl") or _public_url(board, external_path), source=SOURCE,
        location=loc, locations=[loc] if loc else [], remote=remote_from_text(loc, info.get("remoteType")),
        employment_type=info.get("timeType"), posted_at=parse_date(info.get("startDate")) or posted_on_to_date(info.get("postedOn")),
        apply_url=info.get("externalUrl"), description=info.get("jobDescription") or "", job_id=external_path,
    )
