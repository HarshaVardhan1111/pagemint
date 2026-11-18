"""Find where a company's jobs live."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from pagemint.jobs.model import Board

_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_WORKDAY_HOST_RE = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$")
_RECRUITEE_HOST_RE = re.compile(r"^([a-z0-9-]+)\.recruitee\.com$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _parts(url: str):
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return p.hostname.lower(), [s for s in p.path.split("/") if s], p.query


def _ref(url: str) -> tuple[Board, str | None] | None:
    parts = _parts(url)
    if parts is None:
        return None
    host, segs, query = parts

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"):
        if segs[:1] == ["embed"]:
            token = (parse_qs(query).get("for") or [""])[0]
            return (Board("greenhouse", token), None) if token else None
        if not segs or not _SLUG_RE.match(segs[0]):
            return None
        job = segs[2] if len(segs) >= 3 and segs[1] == "jobs" else None
        return Board("greenhouse", segs[0]), job

    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        if not segs:
            return None
        api = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
        return Board("lever", segs[0], host=api), (segs[1] if len(segs) >= 2 else None)

    if host == "jobs.ashbyhq.com":
        if not segs:
            return None
        return Board("ashby", segs[0]), (segs[1] if len(segs) >= 2 else None)

    m = _WORKDAY_HOST_RE.match(host)
    if m:
        rest = segs[1:] if segs and _LANG_RE.match(segs[0]) else segs
        if not rest:
            return None
        site = rest[0]
        job = "/" + "/".join(rest[1:]) if len(rest) >= 3 and rest[1] == "job" else None
        return Board("workday", m.group(1), host=host, site=site), job

    m = _RECRUITEE_HOST_RE.match(host)
    if m:
        job = segs[1] if len(segs) >= 2 and segs[0] == "o" else None
        return Board("recruitee", m.group(1), host=host), job

    if host == "apply.workable.com" and segs:
        return Board("workable", segs[0], careers_url=url), (segs[2] if len(segs) >= 3 and segs[1] == "j" else None)
    return None


def board_from_url(url: str) -> Board | None:
    """Recognise a hiring-system board from a careers or job URL."""
    ref = _ref(url)
    return ref[0] if ref else None


def job_ref_from_url(url: str) -> tuple[Board, str] | None:
    """(board, job id) for a single-job URL on a known hiring system."""
    ref = _ref(url)
    if not ref or not ref[1]:
        return None
    return ref[0], ref[1]
