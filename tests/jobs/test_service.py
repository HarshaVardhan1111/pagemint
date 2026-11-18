import json

import pytest

from conftest import Response, make_settings
from fixtures import GREENHOUSE, GREENHOUSE_JOB, LEVER, js
from pagemint.client import PageMint
from pagemint.jobs import service as service_mod
from pagemint.jobs.readers import ashby, greenhouse, lever
from pagemint.jobs.service import JobService
from pagemint.jobs.sources import arbeitnow, himalayas, hn
from pagemint.jobs.store import JobStore

JSON = {"Content-Type": "application/json"}
ARBEITNOW = {"data": [{"slug": "py", "company_name": "Euro GmbH", "title": "Python Entwickler", "description": "Python",
                       "remote": True, "url": "https://www.arbeitnow.com/jobs/py", "job_types": [], "location": "Berlin",
                       "created_at": 1795039233}], "links": {"next": None}}


@pytest.fixture
def base(server, monkeypatch):
    root = server.url("").rstrip("/")
    for mod in (greenhouse, ashby, hn, arbeitnow, himalayas):
        monkeypatch.setattr(mod, "BASE", root)
    monkeypatch.setattr(lever, "HOST_OVERRIDE", root)
    server.route("/api/v1/search_by_date", Response(js({"hits": []}), headers=JSON))
    server.route("/api/job-board-api", Response(js(ARBEITNOW), headers=JSON))
    server.route("/jobs/api/search", Response(js({"jobs": []}), headers=JSON))
    return server


@pytest.fixture
async def svc(tmp_path):
    client = PageMint(make_settings(jobs_search_budget=5))
    s = JobService(client, JobStore(str(tmp_path / "jobs.sqlite3")))
    yield s
    await client.aclose()


async def test_company_jobs_saves_and_ranks(base, svc):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    r = await svc.company_jobs("Acme", query="python")
    assert r["company"]["system"] == "greenhouse" and r["company"]["name"] == "Acme Corp"
    assert r["total_open"] == 2 and [j["title"] for j in r["jobs"]] == ["Senior Python Engineer"]
    assert [c["name"] for c in (await svc.saved_companies())["companies"]] == ["Acme Corp"]
    # second call uses the saved board and the cache: no new request
    hits = base.hits("/v1/boards/acme/jobs?content=true")
    await svc.company_jobs("Acme Corp")
    assert base.hits("/v1/boards/acme/jobs?content=true") == hits


async def test_company_jobs_not_found(base, svc):
    r = await svc.company_jobs("Nobody Here")
    assert r["error"]["type"] == "not_found" and "careers-page URL" in r["error"]["message"]


async def test_search_across_saved_companies_and_sources(base, svc):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    base.route("/v0/postings/beta", Response(js(LEVER), headers=JSON))
    await svc.saved_companies("add", "Acme")
    await svc.saved_companies("add", "https://jobs.lever.co/beta")
    r = await svc.search_jobs("python")
    titles = [j["title"] for j in r["jobs"]]
    assert set(titles[:2]) == {"Senior Python Engineer", "Python Entwickler"}  # both title matches lead
    assert "Account Executive" not in titles
    assert r["searched"]["companies"] == 2 and set(r["searched"]["sources"]) == {"hn", "arbeitnow", "himalayas"}
    src = {j["title"]: j["status"] for j in r["jobs"]}
    assert src["Senior Python Engineer"] == "open" and src["Python Entwickler"] == "unverified"
    assert any("Arbeitnow" in c for c in r["credits"])
    only = await svc.search_jobs("python", sources="companies")
    assert [j["title"] for j in only["jobs"]] == ["Senior Python Engineer"]


async def test_search_limit_and_bad_args(base, svc):
    assert (await svc.search_jobs("x", sources="everything"))["error"]["type"] == "invalid_argument"
    r = await svc.search_jobs("python", limit=1000)
    assert len(r["jobs"]) <= 100


async def test_search_budget_gives_partial_results(base, tmp_path):
    base.route("/v1/boards/slow/jobs", Response(js(GREENHOUSE), headers=JSON, delay=3))
    client = PageMint(make_settings(jobs_search_budget=1))
    s = JobService(client, JobStore(str(tmp_path / "j.db")))
    from pagemint.jobs.model import Board

    s.store.save_company("Slow", Board("greenhouse", "slow"), "confirmed")
    r = await s.search_jobs("python")
    await client.aclose()
    assert any(x.get("company") == "Slow" and "time" in x["reason"] for x in r["skipped"])
    assert [j["title"] for j in r["jobs"]] == ["Python Entwickler"]


async def test_stale_cache_used_when_refresh_fails(base, svc, monkeypatch):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    await svc.company_jobs("Acme")
    base.route("/v1/boards/acme/jobs", Response("oops", status=500, headers={"Content-Type": "text/plain"}))
    monkeypatch.setattr(service_mod, "cache_seconds", lambda settings: 0)
    r = await svc.search_jobs("python", sources="companies")
    assert [j["title"] for j in r["jobs"]] == ["Senior Python Engineer"]
    assert any("cached" in w for w in r["warnings"])


async def test_check_job_ats_open_and_closed(base, svc):
    base.route("/v1/boards/acme/jobs/101", Response(js(GREENHOUSE_JOB), headers=JSON))
    r = await svc.check_job("https://boards.greenhouse.io/acme/jobs/101")
    assert r["status"] == "open" and r["job"]["title"] == "Senior Python Engineer"
    assert "Build" in r["job"]["description"]
    r2 = await svc.check_job("https://boards.greenhouse.io/acme/jobs/999")
    assert r2["status"] == "closed" and r2["job"] is None


async def test_check_job_pages(base, svc):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Analyst", "validThrough": "2099-01-01"}
    expired = dict(posting, validThrough="2020-01-01")
    ld = lambda p: f'<html><head><script type="application/ld+json">{json.dumps(p)}</script></head><body>x</body></html>'  # noqa: E731
    base.route("/open", ld(posting))
    base.route("/expired", ld(expired))
    base.route("/plain", "<html><body>A job, maybe</body></html>")
    base.route("/moved", Response(b"", status=302, headers={"Location": "/listing"}))
    base.route("/listing", "<html><body>All jobs</body></html>")
    assert (await svc.check_job(base.url("/open")))["status"] == "open"
    e = await svc.check_job(base.url("/expired"))
    assert e["status"] == "closed" and "2020-01-01" in e["reason"]
    assert (await svc.check_job(base.url("/gone")))["status"] == "closed"
    assert (await svc.check_job(base.url("/plain")))["status"] == "unknown"
    m = await svc.check_job(base.url("/moved"))
    assert m["status"] == "unknown" and "redirect" in m["reason"]
    assert (await svc.check_job("ftp://x"))["error"]["type"] == "invalid_url"


async def test_saved_companies_actions(base, svc):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    added = await svc.saved_companies("add", "Acme")
    assert added["added"]["name"] == "Acme Corp" and "jobs" not in added
    assert (await svc.saved_companies("list"))["count"] == 1
    assert (await svc.saved_companies("remove", "Acme Corp"))["removed"] is True
    assert (await svc.saved_companies("list"))["count"] == 0
    assert (await svc.saved_companies("rename", "x"))["error"]["type"] == "invalid_argument"
    assert (await svc.saved_companies("add"))["error"]["type"] == "invalid_argument"
