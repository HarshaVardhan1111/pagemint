"""Saved companies and cached job feeds, in a local SQLite file. Nothing leaves the machine."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from pagemint.errors import InvalidArgumentError
from pagemint.jobs.model import Board, Job

MAX_COMPANIES = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'local',
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    system TEXT NOT NULL,
    board TEXT NOT NULL,
    host TEXT,
    site TEXT,
    careers_url TEXT,
    confidence TEXT,
    added_at REAL NOT NULL,
    last_refreshed REAL,
    last_error TEXT,
    job_count INTEGER,
    UNIQUE (user_id, key)
);
CREATE TABLE IF NOT EXISTS job_cache (
    key TEXT PRIMARY KEY,
    jobs_json TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


def default_data_dir() -> Path:
    env = os.environ.get("PAGEMINT_DATA_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "pagemint"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pagemint"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pagemint"


def company_key(board: Board) -> str:
    """Stable identity of a board, also used as the job-cache key."""
    parts = [board.system, board.board.lower()]
    if board.host:
        parts.append(board.host.lower())
    if board.site:
        parts.append(board.site)
    return ":".join(parts)


class JobStore:
    """One SQLite connection guarded by a lock; falls back to memory if the file cannot be used."""

    def __init__(self, path: str | None):
        self.warning: str | None = None
        self._lock = threading.Lock()
        self._conn = None
        if path:
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = self._open(path)
                self._conn.execute("PRAGMA journal_mode=WAL")
            except (OSError, sqlite3.Error) as e:
                self._conn = None
                self.warning = (
                    f"Could not use the data file {path} ({e}); saved companies are kept in memory "
                    "until PageMint stops. Set PAGEMINT_DATA_DIR to a writable folder."
                )
        if self._conn is None:
            self._conn = self._open(":memory:")
        self._migrate()
        self._conn.executescript(_SCHEMA)

    def _migrate(self) -> None:
        """Files from before per-user lists: rebuild companies with a user_id (rows become 'local')."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(companies)").fetchall()]
        if not cols or "user_id" in cols:
            return
        with self._lock:
            self._conn.executescript(
                "ALTER TABLE companies RENAME TO companies_old;"
                + _SCHEMA
                + "INSERT INTO companies (id, user_id, key, name, system, board, host, site, careers_url, confidence,"
                "  added_at, last_refreshed, last_error, job_count)"
                " SELECT id, 'local', key, name, system, board, host, site, careers_url, confidence,"
                "  added_at, last_refreshed, last_error, job_count FROM companies_old;"
                "DROP TABLE companies_old;"
            )

    @staticmethod
    def _open(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _exec(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- companies -----------------------------------------------------

    def save_company(self, name: str, board: Board, confidence: str | None, user_id: str = "local") -> int:
        key = company_key(board)
        with self._lock:
            row = self._conn.execute("SELECT id FROM companies WHERE key = ? AND user_id = ?", (key, user_id)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE companies SET name = ?, careers_url = COALESCE(?, careers_url), confidence = ? WHERE id = ?",
                    (name, board.careers_url, confidence, row["id"]),
                )
                return int(row["id"])
            count = self._conn.execute("SELECT COUNT(*) AS n FROM companies WHERE user_id = ?", (user_id,)).fetchone()["n"]
            if count >= MAX_COMPANIES:
                raise InvalidArgumentError(
                    f"The saved list is full ({MAX_COMPANIES} companies). Remove some with saved_companies(action='remove')."
                )
            cur = self._conn.execute(
                "INSERT INTO companies (user_id, key, name, system, board, host, site, careers_url, confidence, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, key, name, board.system, board.board, board.host, board.site, board.careers_url, confidence, time.time()),
            )
            return int(cur.lastrowid)

    def _find(self, name_or_id: str, user_id: str) -> sqlite3.Row | None:
        s = str(name_or_id).strip()
        if s.isdigit():
            rows = self._exec("SELECT * FROM companies WHERE id = ? AND user_id = ?", (int(s), user_id))
            if rows:
                return rows[0]
        rows = self._exec(
            "SELECT * FROM companies WHERE user_id = ? AND (lower(name) = lower(?) OR lower(board) = lower(?)) ORDER BY id",
            (user_id, s, s),
        )
        return rows[0] if rows else None

    def get_company(self, name_or_id: str, user_id: str = "local") -> dict | None:
        row = self._find(name_or_id, user_id)
        return dict(row) if row else None

    def remove_company(self, name_or_id: str, user_id: str = "local") -> bool:
        row = self._find(name_or_id, user_id)
        if not row:
            return False
        self._exec("DELETE FROM companies WHERE id = ?", (row["id"],))
        if not self._exec("SELECT 1 FROM companies WHERE key = ? LIMIT 1", (row["key"],)):
            self._exec("DELETE FROM job_cache WHERE key = ?", (row["key"],))  # nobody else uses this feed
        return True

    def list_companies(self, user_id: str = "local") -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM companies WHERE user_id = ? ORDER BY lower(name)", (user_id,))]

    @staticmethod
    def board_of(company: dict) -> Board:
        return Board(company["system"], company["board"], host=company.get("host"), site=company.get("site"),
                     careers_url=company.get("careers_url"))

    # ---- job cache -----------------------------------------------------

    def cache_jobs(self, key: str, jobs: list[Job]) -> None:
        now = time.time()
        payload = json.dumps([asdict(j) for j in jobs], ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_cache (key, jobs_json, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET jobs_json = excluded.jobs_json, fetched_at = excluded.fetched_at",
                (key, payload, now),
            )
            self._conn.execute(
                "UPDATE companies SET last_refreshed = ?, job_count = ?, last_error = NULL WHERE key = ?",
                (now, len(jobs), key),
            )

    def cached_jobs(self, key: str, max_age_s: float | None) -> tuple[list[Job], float] | None:
        rows = self._exec("SELECT jobs_json, fetched_at FROM job_cache WHERE key = ?", (key,))
        if not rows:
            return None
        age = time.time() - rows[0]["fetched_at"]
        if max_age_s is not None and age > max_age_s:
            return None
        return [Job.from_dict(d) for d in json.loads(rows[0]["jobs_json"])], age

    def drop_cache(self, key: str) -> None:
        self._exec("DELETE FROM job_cache WHERE key = ?", (key,))
        self._exec("UPDATE companies SET job_count = 0 WHERE key = ?", (key,))

    def set_error(self, key: str, message: str) -> None:
        self._exec("UPDATE companies SET last_error = ? WHERE key = ?", (message, key))
