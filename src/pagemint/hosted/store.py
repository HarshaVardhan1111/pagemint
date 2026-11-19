"""Users, OAuth clients, codes, tokens and usage for hosted PageMint (SQLite).

Codes, tokens and login states are stored only as SHA-256 hashes: someone who
reads the database file cannot use them.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    github_id INTEGER NOT NULL UNIQUE,
    login TEXT NOT NULL,
    created_at REAL NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auth_codes (hash TEXT PRIMARY KEY, data_json TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT,
    client_id TEXT,
    data_json TEXT NOT NULL,
    expires_at REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tokens_owner ON tokens (subject, client_id);
CREATE TABLE IF NOT EXISTS pending (hash TEXT PRIMARY KEY, data_json TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (
    user_id TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (user_id, day, kind)
);
"""


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HostedStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(users)").fetchall()]
        if "blocked" not in cols:
            self._conn.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")

    def _exec(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- users and clients ---------------------------------------------

    def upsert_user(self, github_id: int, login: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT id FROM users WHERE github_id = ?", (int(github_id),)).fetchone()
            if row:
                self._conn.execute("UPDATE users SET login = ? WHERE id = ?", (login, row["id"]))
                return row["id"]
            uid = "u_" + secrets.token_hex(8)
            self._conn.execute("INSERT INTO users (id, github_id, login, created_at) VALUES (?, ?, ?, ?)",
                               (uid, int(github_id), login, time.time()))
            return uid

    def get_user(self, user_id: str) -> dict | None:
        rows = self._exec("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(rows[0]) if rows else None

    def get_user_by_github_id(self, github_id: int) -> dict | None:
        rows = self._exec("SELECT * FROM users WHERE github_id = ?", (int(github_id),))
        return dict(rows[0]) if rows else None

    def set_blocked(self, login: str, blocked: bool) -> bool:
        """Block or unblock a user by GitHub username; blocking also revokes all their tokens."""
        rows = self._exec("SELECT id FROM users WHERE lower(login) = lower(?)", (login,))
        if not rows:
            return False
        uid = rows[0]["id"]
        self._exec("UPDATE users SET blocked = ? WHERE id = ?", (1 if blocked else 0, uid))
        if blocked:
            self._exec("UPDATE tokens SET revoked = 1 WHERE subject = ?", (uid,))
        return True

    def count(self, table: str) -> int:
        if table not in ("clients", "pending", "tokens", "auth_codes", "users"):
            raise ValueError(table)
        return int(self._exec(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])

    def save_client(self, client_id: str, info: dict) -> None:
        self._exec("INSERT OR REPLACE INTO clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
                   (client_id, json.dumps(info), time.time()))

    def get_client(self, client_id: str) -> dict | None:
        rows = self._exec("SELECT info_json FROM clients WHERE client_id = ?", (client_id,))
        return json.loads(rows[0]["info_json"]) if rows else None

    # ---- one-time values -------------------------------------------------

    def put_code(self, code: str, data: dict, expires_at: float) -> None:
        self._exec("INSERT INTO auth_codes (hash, data_json, expires_at) VALUES (?, ?, ?)",
                   (_h(code), json.dumps(data), expires_at))

    def _take(self, table: str, value: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"SELECT data_json, expires_at FROM {table} WHERE hash = ?", (_h(value),)).fetchone()
            self._conn.execute(f"DELETE FROM {table} WHERE hash = ?", (_h(value),))
        if not row or row["expires_at"] < time.time():
            return None
        return json.loads(row["data_json"])

    def take_code(self, code: str) -> dict | None:
        return self._take("auth_codes", code)

    def peek_code(self, code: str) -> dict | None:
        rows = self._exec("SELECT data_json, expires_at FROM auth_codes WHERE hash = ?", (_h(code),))
        if not rows or rows[0]["expires_at"] < time.time():
            return None
        return json.loads(rows[0]["data_json"])

    def put_pending(self, state: str, data: dict, expires_at: float) -> None:
        self._exec("INSERT INTO pending (hash, data_json, expires_at) VALUES (?, ?, ?)",
                   (_h(state), json.dumps(data), expires_at))

    def take_pending(self, state: str) -> dict | None:
        return self._take("pending", state)

    # ---- tokens ----------------------------------------------------------

    def put_token(self, token: str, kind: str, data: dict, expires_at: float) -> None:
        self._exec(
            "INSERT INTO tokens (hash, kind, subject, client_id, data_json, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_h(token), kind, data.get("subject"), data.get("client_id"), json.dumps(data), expires_at),
        )

    def get_token(self, token: str, kind: str) -> dict | None:
        rows = self._exec("SELECT data_json, expires_at, revoked FROM tokens WHERE hash = ? AND kind = ?",
                          (_h(token), kind))
        if not rows or rows[0]["revoked"] or rows[0]["expires_at"] < time.time():
            return None
        return json.loads(rows[0]["data_json"])

    def revoke_token(self, token: str) -> None:
        self._exec("UPDATE tokens SET revoked = 1 WHERE hash = ?", (_h(token),))

    def revoke_all_for(self, subject: str, client_id: str) -> None:
        self._exec("UPDATE tokens SET revoked = 1 WHERE subject = ? AND client_id = ?", (subject, client_id))

    # ---- usage -----------------------------------------------------------

    def add_usage(self, user_id: str, day: str, kind: str, n: int) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (user_id, day, kind, count) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, day, kind) DO UPDATE SET count = count + excluded.count",
                (user_id, day, kind, n),
            )
            row = self._conn.execute("SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?",
                                     (user_id, day, kind)).fetchone()
        return int(row["count"])

    def get_usage(self, user_id: str, day: str, kind: str) -> int:
        rows = self._exec("SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?", (user_id, day, kind))
        return int(rows[0]["count"]) if rows else 0

    def purge_expired(self) -> int:
        now = time.time()
        n = 0
        with self._lock:
            for table in ("auth_codes", "pending", "tokens"):
                n += self._conn.execute(f"DELETE FROM {table} WHERE expires_at < ?", (now,)).rowcount
        return n
