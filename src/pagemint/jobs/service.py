"""Job search on top of PageMint: the four job tools."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timezone

from pagemint.config import Settings
from pagemint.errors import InvalidArgumentError, PageMintError, error_dict
from pagemint.jobs import detect
from pagemint.jobs.jsonld import parse_jobpostings, read_careers_page
from pagemint.jobs.model import Board, Job
from pagemint.jobs.rank import filter_and_rank
from pagemint.jobs.readers import READERS
from pagemint.jobs.readers._common import BoardGoneError
from pagemint.jobs.sources import SOURCES
from pagemint.jobs.store import JobStore, company_key

log = logging.getLogger("pagemint.jobs")

SEARCH_LIMIT_MAX = 100
COMPANY_LIMIT_MAX = 50
SOURCE_CACHE_MAX_S = 3600  # free sources update often; cache them for at most an hour
SOURCE_MODES = ("all", "companies")
STALE_MAX_S = 48 * 3600  # never serve cached jobs older than this when a refresh fails


def cache_seconds(settings: Settings) -> float:
    return settings.jobs_cache_hours * 3600


def _clamp(n, lo, hi, default):
    try:
        return max(lo, min(hi, int(n)))
    except (TypeError, ValueError):
        return default


def _ago(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.0f} hours" if hours >= 1.5 else f"{max(1, round(seconds / 60))} minutes"


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _company_view(c: dict) -> dict:
    return {
        "id": c["id"], "name": c["name"], "system": c["system"], "board": c["board"],
        "careers_url": c.get("careers_url"), "confidence": c.get("confidence"), "job_count": c.get("job_count"),
        "last_refreshed": _iso(c.get("last_refreshed")), "last_error": c.get("last_error"),
    }


class JobService:
    def __init__(self, client, store: JobStore, user_id: str = "local"):
        self.user_id = user_id
        self.client = client
        self.store = store
        self.settings: Settings = client.settings

    # ---- helpers -------------------------------------------------------

    def _warnings(self) -> list[str]:
        return [self.store.warning] if self.store.warning else []

    async def _fetch_board(self, board: Board, deadline: float) -> list[Job]:
        if board.system in READERS:
            jobs, _ = await READERS[board.system].list_jobs(self.client, board)
            return jobs
        # Workable and any other careers page: the general JobPosting reader.
        return await read_careers_page(self.client, board.careers_url or board.board, deadline)

    async def _board_jobs(self, board: Board, deadline: float) -> tuple[list[Job], list[str]]:
        """Jobs for a board: fresh cache, else fetch; on failure fall back to stale cache."""
        key = company_key(board)
        cached = self.store.cached_jobs(key, cache_seconds(self.settings))
        if cached is not None:
            return cached[0], []
        try:
            jobs = await self._fetch_board(board, deadline)
        except BoardGoneError as e:
            # The board itself is gone: its cached jobs are closed, not stale.
            self.store.set_error(key, e.message)
            self.store.drop_cache(key)
            raise
        except PageMintError as e:
            self.store.set_error(key, e.message)
            stale = self.store.cached_jobs(key, STALE_MAX_S)
            if stale is None:
                raise
            for j in stale[0]:
                j.status = "unverified"
            return stale[0], [
                f"{board.board}: could not refresh ({e.message}); showing cached jobs from {_ago(stale[1])} ago "
                "as unverified."
            ]
        self.store.cache_jobs(key, jobs)
        return jobs, []

    async def _source_jobs(self, name: str, query: str) -> list[Job]:
        mod = SOURCES[name]
        key = f"source:{name}" + (f":{query.strip().lower()}" if name == "himalayas" else "")
        cached = self.store.cached_jobs(key, min(cache_seconds(self.settings), SOURCE_CACHE_MAX_S))
        if cached is not None:
            return cached[0]
        jobs = await mod.fetch(self.client, query)
        self.store.cache_jobs(key, jobs)
        return jobs

    async def _resolve(self, company: str) -> tuple[dict, detect.Detection | None]:
        """A saved company row for a name/URL, detecting and saving it if new."""
        is_url = company.strip().lower().startswith(("http://", "https://"))
        if not is_url:
            saved = self.store.get_company(company, user_id=self.user_id)
            if saved:
                return saved, None
        det = await detect.detect_company(self.client, company)
        if det.board is None:
            raise LookupError(det.warnings[0] if det.warnings else f"Could not find '{company}'.")
        cid = self.store.save_company(det.company_name, det.board, det.confidence, user_id=self.user_id)
        if det.jobs is not None:
            self.store.cache_jobs(company_key(det.board), det.jobs)
        return self.store.get_company(str(cid), user_id=self.user_id), det

    # ---- tools ---------------------------------------------------------

    async def company_jobs(self, company: str, query: str = "", location: str = "", remote: bool | None = None,
                           limit: int = COMPANY_LIMIT_MAX) -> dict:
        try:
            if not (company or "").strip():
                raise InvalidArgumentError("Give a company name or a careers-page URL.")
            limit = _clamp(limit, 1, COMPANY_LIMIT_MAX, COMPANY_LIMIT_MAX)
            try:
                row, det = await self._resolve(company)
            except LookupError as e:
                return {"company": company, "error": {"type": "not_found", "message": str(e)}}
            board = JobStore.board_of(row)
            deadline = time.monotonic() + self.settings.jobs_search_budget
            jobs, warnings = await self._board_jobs(board, deadline)
            ranked = filter_and_rank(jobs, query, location, remote)
            return {
                "company": {"name": row["name"], "system": board.system, "board": board.board,
                            "careers_url": board.careers_url, "confidence": row.get("confidence")},
                "jobs": [j.to_dict() for j in ranked[:limit]],
                "total_open": len(jobs),
                "total_matches": len(ranked),
                "warnings": (det.warnings if det else []) + warnings + self._warnings(),
            }
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("company_jobs failed for %s", company)
            return {"company": company, **error_dict(e)}

    async def search_jobs(self, query: str, location: str = "", remote: bool | None = None,
                          posted_within_days: int | None = None, companies: list[str] | None = None,
                          sources: str = "all", limit: int = 25) -> dict:
        try:
            if sources not in SOURCE_MODES:
                raise InvalidArgumentError(f"sources must be one of: {', '.join(SOURCE_MODES)}.")
            limit = _clamp(limit, 1, SEARCH_LIMIT_MAX, 25)
            warnings: list[str] = []
            skipped: list[dict] = []
            deadline = time.monotonic() + self.settings.jobs_search_budget

            rows: list[dict] = []
            if companies:
                if isinstance(companies, str):
                    companies = [companies]
                resolving = {asyncio.ensure_future(self._resolve(n)): n for n in companies}
                done, pending = await asyncio.wait(resolving, timeout=max(0.0, deadline - time.monotonic()))
                for t in pending:
                    t.cancel()
                    skipped.append({"company": resolving[t], "reason": "time limit reached while finding the company"})
                for t in done:
                    exc = t.exception()
                    if isinstance(exc, LookupError):
                        skipped.append({"company": resolving[t], "reason": str(exc)})
                    elif isinstance(exc, PageMintError):
                        skipped.append({"company": resolving[t], "reason": exc.message})
                    elif exc is not None:
                        skipped.append({"company": resolving[t], "reason": f"{type(exc).__name__}: {exc}"})
                    else:
                        row, det = t.result()
                        rows.append(row)
                        if det:
                            warnings.extend(det.warnings)
            else:
                rows = self.store.list_companies(user_id=self.user_id)
            if not rows and sources == "companies":
                warnings.append("No saved companies yet. Use company_jobs or saved_companies(action='add') to add some.")

            tasks: dict[asyncio.Task, tuple[str, str]] = {}
            for row in rows:
                t = asyncio.ensure_future(self._board_jobs(JobStore.board_of(row), deadline))
                tasks[t] = ("company", row["name"])
            used_sources = list(SOURCES) if sources == "all" else []
            for name in used_sources:
                tasks[asyncio.ensure_future(self._source_jobs(name, query))] = ("source", name)

            all_jobs: list[Job] = []
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
                for t in pending:
                    t.cancel()
                    kind, name = tasks[t]
                    skipped.append({kind: name, "reason": "time limit reached before it answered"})
                for t in done:
                    kind, name = tasks[t]
                    exc = t.exception()
                    if exc is not None:
                        msg = exc.message if isinstance(exc, PageMintError) else f"{type(exc).__name__}: {exc}"
                        skipped.append({kind: name, "reason": msg})
                        continue
                    result = t.result()
                    if kind == "company":
                        jobs, w = result
                        warnings.extend(w)
                    else:
                        jobs = result
                    all_jobs.extend(jobs)

            seen: set[str] = set()
            unique = []
            for j in all_jobs:
                if j.url not in seen:
                    seen.add(j.url)
                    unique.append(j)
            ranked = filter_and_rank(unique, query, location, remote, posted_within_days)
            return {
                "jobs": [j.to_dict() for j in ranked[:limit]],
                "total_matches": len(ranked),
                "searched": {"companies": len(rows), "sources": used_sources},
                "skipped": skipped,
                "warnings": warnings + self._warnings(),
                "credits": [SOURCES[s].CREDIT for s in used_sources],
            }
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("search_jobs failed")
            return error_dict(e)

    def _greenhouse_ref(self, url: str) -> tuple[Board, str] | None:
        """Company-hosted Greenhouse job links (e.g. example.com/careers?gh_jid=123): find the board
        among saved Greenhouse companies whose cached jobs include this job."""
        from urllib.parse import parse_qs, urlsplit

        job_id = (parse_qs(urlsplit(url).query).get("gh_jid") or [""])[0]
        if not job_id.isdigit():
            return None
        for row in self.store.list_companies(user_id=self.user_id):
            if row["system"] != "greenhouse":
                continue
            board = JobStore.board_of(row)
            cached = self.store.cached_jobs(company_key(board), None)
            if cached and any(j.job_id == job_id or j.url == url for j in cached[0]):
                return board, job_id
        return None

    async def check_job(self, url: str) -> dict:
        try:
            target = self.client._check_url(url)
            ref = detect.job_ref_from_url(target) or self._greenhouse_ref(target)
            if ref and ref[0].system in READERS:
                board, job_id = ref
                job = await READERS[board.system].get_job(self.client, board, job_id)
                if job is None:
                    return {"url": target, "status": "closed",
                            "reason": f"The {board.system} job is no longer listed.", "job": None}
                return {"url": target, "status": "open", "reason": f"Listed on {board.system}.",
                        "job": job.to_dict(snippet=False)}
            res = await self.client.fetcher.fetch(target)
            if res.status_code in (404, 410):
                return {"url": target, "status": "closed", "reason": f"The page answers HTTP {res.status_code}.", "job": None}
            postings = parse_jobpostings(res.text, res.url, today=date.min)
            today = date.today().isoformat()
            if postings:
                live = [p for p in postings if not p.valid_through or p.valid_through >= today]
                if not live:
                    return {"url": target, "status": "closed",
                            "reason": f"The job data says it expired on {postings[0].valid_through}.", "job": None}
                return {"url": target, "status": "open", "reason": "The page carries current job data.",
                        "job": live[0].to_dict(snippet=False)}
            if res.redirects:
                return {"url": target, "status": "unknown",
                        "reason": f"The link redirected to {res.url} with no job data (probably closed).", "job": None}
            return {"url": target, "status": "unknown",
                    "reason": "The page loads but has no job data to confirm the job is open.", "job": None}
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("check_job failed for %s", url)
            return {"url": url, **error_dict(e)}

    async def saved_companies(self, action: str = "list", company: str | None = None) -> dict:
        try:
            if action not in ("list", "add", "remove"):
                raise InvalidArgumentError("action must be 'list', 'add' or 'remove'.")
            result: dict = {}
            if action in ("add", "remove") and not (company or "").strip():
                raise InvalidArgumentError(f"Give the company to {action}.")
            if action == "add":
                try:
                    row, det = await self._resolve(company)
                except LookupError as e:
                    return {"error": {"type": "not_found", "message": str(e)}}
                result["added"] = _company_view(row)
                if det and det.warnings:
                    result["warnings"] = det.warnings
            elif action == "remove":
                result["removed"] = self.store.remove_company(company, user_id=self.user_id)
            companies = [_company_view(c) for c in self.store.list_companies(user_id=self.user_id)]
            result.update({"companies": companies, "count": len(companies)})
            result["warnings"] = result.get("warnings", []) + self._warnings()
            return result
        except Exception as e:
            if not isinstance(e, PageMintError):
                log.exception("saved_companies failed")
            return error_dict(e)
