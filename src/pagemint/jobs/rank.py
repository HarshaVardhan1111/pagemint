"""Deterministic keyword ranking and filtering (no AI)."""

from __future__ import annotations

import re
from datetime import date, timedelta

from pagemint.jobs.model import Job

_WORD_RE = re.compile(r"[a-z0-9+#.]+")
STOP_WORDS = {
    "a", "an", "and", "the", "in", "on", "at", "for", "of", "to", "with", "or", "job", "jobs", "role", "roles",
    "position", "positions", "opening", "openings", "remote", "hiring", "near", "me",
}
LOCATION_ALIASES = [
    {"bengaluru", "bangalore"},
    {"gurugram", "gurgaon"},
    {"mumbai", "bombay"},
    {"new york", "nyc", "new york city"},
    {"san francisco", "sf", "bay area"},
    {"united kingdom", "uk", "great britain", "england"},
    {"united states", "us", "usa", "u.s.", "united states of america"},
    {"chennai", "madras"},
    {"kolkata", "calcutta"},
]
DESC_CAP = 5


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(text: str) -> list[str]:
    return [_stem(w.strip(".")) for w in _WORD_RE.findall((text or "").lower()) if w.strip(".") and w not in STOP_WORDS]


def _location_variants(location: str) -> set[str]:
    loc = location.strip().lower()
    for group in LOCATION_ALIASES:
        if loc in group:
            return set(group)
    return {loc}


def _matches_location(job: Job, wanted: set[str]) -> bool:
    haystack = " | ".join([job.location or "", *job.locations]).lower()
    for variant in wanted:
        if len(variant) <= 3:  # short codes like "us", "uk", "sf": whole-word match
            if re.search(rf"(?<![a-z]){re.escape(variant)}(?![a-z])", haystack):
                return True
        elif variant in haystack:
            return True
    return False


def score(job: Job, query_terms: list[str], phrase: str, today: date) -> float:
    s = 0.0
    if query_terms:
        title_terms = set(terms(job.title))
        hits = [t for t in query_terms if t in title_terms]
        s += 5 * len(hits)
        if len(hits) == len(query_terms):
            s += 5
        if phrase and phrase in " ".join(terms(job.title)):
            s += 5
        dept_terms = set(terms(job.department or ""))
        s += 2 * sum(1 for t in query_terms if t in dept_terms)
        desc_terms = set(terms(job.text()[:5000]))
        s += min(DESC_CAP, sum(1 for t in query_terms if t in desc_terms))
    return s


def _recency(job: Job, today: date) -> float:
    if not job.posted_at:
        return 0.0
    try:
        age = (today - date.fromisoformat(job.posted_at)).days
    except ValueError:
        return 0.0
    if age < 0 or age > 30:
        return 0.0
    return 3.0 * (30 - age) / 30


def filter_and_rank(
    jobs: list[Job],
    query: str = "",
    location: str = "",
    remote: bool | None = None,
    posted_within_days: int | None = None,
    today: date | None = None,
) -> list[Job]:
    today = today or date.today()
    q_terms = terms(query)
    phrase = " ".join(q_terms) if len(q_terms) > 1 else ""
    wanted = _location_variants(location) if location.strip() else None
    cutoff = today - timedelta(days=posted_within_days) if posted_within_days is not None else None

    scored: list[tuple[float, str, Job]] = []
    for job in jobs:
        if remote is True and job.remote is not True:
            continue
        if remote is False and job.remote is True:
            continue
        if wanted is not None and not _matches_location(job, wanted):
            if not (job.remote is True and remote is not False):
                continue
        if cutoff is not None:
            if not job.posted_at or job.posted_at < cutoff.isoformat():
                continue
        s = score(job, q_terms, phrase, today)
        if q_terms and s <= 0:
            continue
        scored.append((s + _recency(job, today), job.posted_at or "", job))
    # Best score first; ties newest first; undated last.
    scored.sort(key=lambda t: (-t[0], t[1] == "", _neg_date(t[1])))
    return [j for _, _, j in scored]


def _neg_date(d: str) -> int:
    try:
        return -date.fromisoformat(d).toordinal()
    except ValueError:
        return 0
