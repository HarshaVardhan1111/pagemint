"""Find where a company's jobs live."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pagemint.errors import PageMintError
from pagemint.jobs.model import Board, Job

if TYPE_CHECKING:
    from pagemint.client import PageMint

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


# ---- detection by name or careers page --------------------------------------

log = logging.getLogger("pagemint.jobs")

_WORDS_RE = re.compile(r"[a-z0-9]+")
LEGAL_WORDS = {"inc", "ltd", "llc", "gmbh", "corp", "corporation", "co", "pvt", "private", "limited", "plc",
               "sa", "ag", "bv", "the"}
DESCRIPTIVE_WORDS = {"technologies", "technology", "tech", "labs", "lab", "software", "systems", "solutions",
                     "group", "holdings", "hq"}
_ATS_IN_HTML_RE = re.compile(
    r"""(?:https?:)?//(?:"""
    r"""(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/[^"'\s<>\\]+"""
    r"""|jobs(?:\.eu)?\.lever\.co/[^"'\s<>\\]+"""
    r"""|jobs\.ashbyhq\.com/[^"'\s<>\\]+"""
    r"""|[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^"'\s<>\\]+"""
    r"""|[a-z0-9-]+\.recruitee\.com[^"'\s<>\\]*"""
    r"""|apply\.workable\.com/[^"'\s<>\\]+)""",
    re.I,
)
PROBE_SYSTEMS = ("greenhouse", "lever", "lever_eu", "ashby")


@dataclass
class Detection:
    board: Board | None
    company_name: str
    confidence: str | None  # confirmed | guessed | None
    warnings: list[str] = field(default_factory=list)
    jobs: list[Job] | None = None  # jobs fetched while detecting (reused by the caller)


def _words(name: str) -> list[str]:
    return [w for w in _WORDS_RE.findall(name.lower()) if w not in LEGAL_WORDS]


def slug_candidates(name: str) -> list[str]:
    core = _words(name)
    base = [w for w in core if w not in DESCRIPTIVE_WORDS] or core
    out: list[str] = []
    for cand in ("".join(base), "".join(core), "-".join(core), "-".join(base)):
        if cand and cand not in out:
            out.append(cand)
    return out[:3]


def names_match(given: str, found: str) -> bool:
    a = [w for w in _words(given) if w not in DESCRIPTIVE_WORDS] or _words(given)
    b = [w for w in _words(found) if w not in DESCRIPTIVE_WORDS] or _words(found)
    if not a or not b:
        return False
    if "".join(a) == "".join(b):
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_[: len(short)] == short


def _probe_board(system: str, slug: str) -> Board:
    if system == "lever":
        return Board("lever", slug, host="api.lever.co")
    if system == "lever_eu":
        return Board("lever", slug, host="api.eu.lever.co")
    return Board(system, slug)


async def _list(client: "PageMint", board: Board) -> tuple[list[Job], str | None] | None:
    from pagemint.jobs.readers import READERS

    try:
        jobs, name = await READERS[board.system].list_jobs(client, board)
    except PageMintError as e:
        log.debug("probe %s/%s failed: %s", board.system, board.board, e.message)
        return None
    return jobs, name


async def _detect_by_name(client: "PageMint", name: str) -> Detection:
    for slug in slug_candidates(name):
        boards = [_probe_board(s, slug) for s in PROBE_SYSTEMS]
        results = await asyncio.gather(*(_list(client, b) for b in boards))
        found = []
        for board, res in zip(boards, results):
            if not res or not res[0]:
                continue
            jobs, company = res
            confidence = "confirmed" if company and names_match(name, company) else "guessed"
            found.append((confidence == "confirmed", len(jobs), board, jobs, company))
        if not found:
            continue
        found.sort(key=lambda f: (f[0], f[1]), reverse=True)
        confirmed, _, board, jobs, company = found[0]
        warnings = []
        if not confirmed:
            pretty = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}[board.system]
            warnings.append(
                f"Found a {pretty} board '{board.board}'. If this is not the company you meant, "
                "pass its careers-page URL."
            )
        for _, n, other, _, _ in found[1:]:
            warnings.append(f"Also found a {other.system} board '{other.board}' with {n} jobs; using {board.system}.")
        return Detection(board, company or name, "confirmed" if confirmed else "guessed", warnings, jobs)
    return Detection(None, name, None, [
        f"Could not find '{name}' on Greenhouse, Lever or Ashby. Pass its careers-page URL instead "
        "(Workday and other hiring systems need the URL)."
    ])


async def _detect_by_url(client: "PageMint", url: str) -> Detection:
    board = board_from_url(url)
    careers_url = None
    if board is None:
        res = await client.fetcher.fetch(url)
        for m in _ATS_IN_HTML_RE.finditer(res.text):
            link = m.group(0)
            link = ("https:" + link) if link.startswith("//") else link
            board = board_from_url(link.replace("&amp;", "&"))
            if board is not None:
                careers_url = res.url
                break
        if board is None:
            return Detection(Board("jsonld", res.url, careers_url=res.url), urlsplit(res.url).hostname or url, "confirmed")
    if careers_url:
        board.careers_url = careers_url
    if board.system == "workable":
        return Detection(board, board.board, "confirmed")
    listed = await _list(client, board)
    if listed is None:
        return Detection(board, board.board, "confirmed", [f"Could not read the {board.system} board '{board.board}' right now."], None)
    jobs, name = listed
    return Detection(board, name or board.board, "confirmed", [], jobs)


async def detect_company(client: "PageMint", company: str) -> Detection:
    """Find a company's hiring system from its name or a careers/job-board URL."""
    company = (company or "").strip()
    if company.lower().startswith(("http://", "https://")):
        return await _detect_by_url(client, company)
    return await _detect_by_name(client, company)
