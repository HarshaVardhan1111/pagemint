import os
import sys
import threading
import time

import pytest

from pagemint.errors import InvalidArgumentError
from pagemint.jobs.model import Board, Job
from pagemint.jobs.store import JobStore, company_key, default_data_dir


def job(i):
    return Job(title=f"Job {i}", company="Acme", url=f"https://x/{i}", source="greenhouse", posted_at="2026-11-01")


def test_company_round_trip(tmp_path):
    s = JobStore(str(tmp_path / "jobs.sqlite3"))
    b = Board("lever", "acme", host="api.lever.co")
    cid = s.save_company("Acme", b, "guessed")
    assert s.save_company("Acme", b, "guessed") == cid  # same board: updated, not duplicated
    [c] = s.list_companies()
    assert (c["name"], c["system"], c["board"], c["host"], c["confidence"]) == ("Acme", "lever", "acme", "api.lever.co", "guessed")
    assert s.get_company("acme")["id"] == cid
    assert JobStore.board_of(c) == b
    # persists across instances
    assert JobStore(str(tmp_path / "jobs.sqlite3")).get_company("ACME")["id"] == cid
    assert s.remove_company("Acme") and s.list_companies() == []
    assert not s.remove_company("Acme")


def test_remove_by_id(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    cid = s.save_company("Acme", Board("greenhouse", "acme"), "confirmed")
    assert s.remove_company(str(cid))


def test_cache_ttl_and_stale_read(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    b = Board("greenhouse", "acme")
    s.save_company("Acme", b, "confirmed")
    key = company_key(b)
    s.cache_jobs(key, [job(1), job(2)])
    jobs, age = s.cached_jobs(key, max_age_s=60)
    assert [j.title for j in jobs] == ["Job 1", "Job 2"] and age < 5
    assert s.get_company("Acme")["job_count"] == 2 and s.get_company("Acme")["last_refreshed"]
    time.sleep(0.05)
    assert s.cached_jobs(key, max_age_s=0.01) is None
    assert s.cached_jobs(key, max_age_s=None)[0][0].title == "Job 1"
    s.set_error(key, "HTTP 500")
    assert s.get_company("Acme")["last_error"] == "HTTP 500"
    assert s.cached_jobs("source:nope", None) is None


def test_concurrent_writes(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    errors = []

    def work(n):
        try:
            for i in range(20):
                s.cache_jobs(f"source:{n}", [job(i)])
                s.save_company(f"C{n}-{i % 3}", Board("greenhouse", f"c{n}{i % 3}"), "confirmed")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(s.list_companies()) == 30


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_unwritable_dir_falls_back_to_memory(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    s = JobStore(str(ro / "sub" / "j.db"))
    assert s.warning and "memory" in s.warning
    s.save_company("Acme", Board("greenhouse", "acme"), "confirmed")
    assert s.list_companies()


def test_unopenable_path_falls_back_to_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = JobStore(str(blocker / "j.db"))  # parent is a file, cannot create
    assert s.warning and "memory" in s.warning
    s.save_company("Acme", Board("greenhouse", "acme"), "confirmed")
    assert len(s.list_companies()) == 1


def test_cap_500(tmp_path):
    s = JobStore(str(tmp_path / "j.db"))
    for i in range(500):
        s.save_company(f"C{i}", Board("greenhouse", f"c{i}"), "confirmed")
    with pytest.raises(InvalidArgumentError):
        s.save_company("One more", Board("greenhouse", "more"), "confirmed")


def test_default_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PAGEMINT_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path
    monkeypatch.delenv("PAGEMINT_DATA_DIR")
    assert default_data_dir().name == "pagemint"
