import sqlite3
import time

from conftest import make_settings
from pagemint.client import PageMint
from pagemint.jobs.model import Board
from pagemint.jobs.service import JobService
from pagemint.jobs.store import JobStore


def test_companies_are_per_user(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    s.save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u_a")
    s.save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u_b")
    s.save_company("Beta", Board("lever", "beta", host="api.lever.co"), "guessed", user_id="u_b")
    assert [c["name"] for c in s.list_companies(user_id="u_a")] == ["Acme"]
    assert [c["name"] for c in s.list_companies(user_id="u_b")] == ["Acme", "Beta"]
    assert s.list_companies() == []  # the local user has nothing
    assert s.get_company("Beta", user_id="u_a") is None
    assert s.remove_company("Acme", user_id="u_a")
    assert [c["name"] for c in s.list_companies(user_id="u_b")] == ["Acme", "Beta"]


def test_remove_by_id_respects_owner(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    cid = s.save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u_a")
    assert not s.remove_company(str(cid), user_id="u_b")
    assert s.remove_company(str(cid), user_id="u_a")


def test_cap_is_per_user(tmp_path, monkeypatch):
    import pagemint.jobs.store as store_mod

    monkeypatch.setattr(store_mod, "MAX_COMPANIES", 2)
    s = JobStore(str(tmp_path / "j.db"))
    s.save_company("A", Board("greenhouse", "a"), "confirmed", user_id="u_a")
    s.save_company("B", Board("greenhouse", "b"), "confirmed", user_id="u_a")
    s.save_company("A", Board("greenhouse", "a"), "confirmed", user_id="u_b")  # other user unaffected


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            system TEXT NOT NULL, board TEXT NOT NULL, host TEXT, site TEXT, careers_url TEXT, confidence TEXT,
            added_at REAL NOT NULL, last_refreshed REAL, last_error TEXT, job_count INTEGER);
        CREATE TABLE job_cache (key TEXT PRIMARY KEY, jobs_json TEXT NOT NULL, fetched_at REAL NOT NULL);
    """)
    conn.execute("INSERT INTO companies (key, name, system, board, added_at) VALUES ('greenhouse:acme', 'Acme', 'greenhouse', 'acme', ?)",
                 (time.time(),))
    conn.commit()
    conn.close()
    s = JobStore(str(path))
    assert [c["name"] for c in s.list_companies()] == ["Acme"]
    s.save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u_x")
    assert len(s.list_companies(user_id="u_x")) == 1 and len(s.list_companies()) == 1


async def test_job_service_uses_its_user(tmp_path):
    store = JobStore(str(tmp_path / "j.db"))
    async with PageMint(make_settings()) as c:
        a, b = JobService(c, store, user_id="u_a"), JobService(c, store, user_id="u_b")
        store.save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u_a")
        assert (await a.saved_companies())["count"] == 1
        assert (await b.saved_companies())["count"] == 0
        assert (await b.saved_companies("remove", "Acme"))["removed"] is False
