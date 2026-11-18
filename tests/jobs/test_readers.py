import json
from datetime import date, timedelta

import pytest

from conftest import Response
from fixtures import ASHBY, GREENHOUSE, GREENHOUSE_JOB, LEVER, RECRUITEE, WORKDAY_DETAIL, js, workday_page
from pagemint.errors import FetchError
from pagemint.jobs.model import Board
from pagemint.jobs.readers import READERS, ashby, greenhouse, lever, recruitee, workday

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def base(server, monkeypatch):
    root = server.url("").rstrip("/")
    for mod in (greenhouse, ashby):
        monkeypatch.setattr(mod, "BASE", root)
    monkeypatch.setattr(lever, "HOST_OVERRIDE", root)
    monkeypatch.setattr(workday, "HOST_OVERRIDE", root)
    monkeypatch.setattr(recruitee, "HOST_OVERRIDE", root)
    return server


def test_registry():
    assert set(READERS) == {"greenhouse", "lever", "ashby", "workday", "recruitee"}


async def test_greenhouse_list_and_fields(base, client):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    jobs, name = await greenhouse.list_jobs(client, Board("greenhouse", "acme"))
    assert name == "Acme Corp" and len(jobs) == 2
    j = jobs[0]
    assert (j.title, j.company, j.location, j.department, j.posted_at, j.source) == (
        "Senior Python Engineer", "Acme Corp", "Bengaluru, India", "Engineering", "2026-11-01", "greenhouse")
    assert j.url == "https://boards.greenhouse.io/acme/jobs/101" and j.job_id == "101"
    snippet = j.to_dict()["description"]
    assert snippet == "Build data tools & APIs."  # escaped HTML cleaned (Review Focus 1)
    assert jobs[1].remote is True and jobs[1].posted_at == "2026-10-01"


async def test_greenhouse_unknown_board(base, client):
    with pytest.raises(FetchError, match="not found"):
        await greenhouse.list_jobs(client, Board("greenhouse", "nope"))


async def test_greenhouse_get_job_and_closed(base, client):
    base.route("/v1/boards/acme/jobs/101", Response(js(GREENHOUSE_JOB), headers=JSON))
    j = await greenhouse.get_job(client, Board("greenhouse", "acme"), "101")
    assert j.salary == {"min": 30000.0, "max": 45000.0, "currency": "INR", "period": None, "text": "Base"}
    assert await greenhouse.get_job(client, Board("greenhouse", "acme"), "999") is None


async def test_lever_list_and_fields(base, client):
    base.route("/v0/postings/acme", Response(js(LEVER), headers=JSON))
    jobs, name = await lever.list_jobs(client, Board("lever", "acme", host="api.lever.co"))
    assert name is None and len(jobs) == 2
    a, b = jobs
    assert (a.title, a.location, a.workplace_type, a.remote, a.employment_type, a.department) == (
        "Backend Engineer (Go)", "Berlin, Germany", "hybrid", False, "Full-time", "Engineering")
    assert a.locations == ["Berlin, Germany", "Remote, Europe"] and a.posted_at == "2026-08-11"
    assert a.salary == {"min": 70000, "max": 90000, "currency": "EUR", "period": "year", "text": None}
    assert a.apply_url.endswith("/apply") and b.remote is True and b.posted_at == "2026-11-18"


async def test_lever_get_job_closed(base, client):
    base.route("/v0/postings/acme/aaa-111", Response(js(LEVER[0]), headers=JSON))
    assert (await lever.get_job(client, Board("lever", "acme", host="api.lever.co"), "aaa-111")).title.startswith("Backend")
    assert await lever.get_job(client, Board("lever", "acme", host="api.lever.co"), "gone") is None


async def test_ashby_list_skips_unlisted(base, client):
    base.route("/posting-api/job-board/acme", Response(js(ASHBY), headers=JSON))
    jobs, _ = await ashby.list_jobs(client, Board("ashby", "acme"))
    assert [j.title for j in jobs] == ["Machine Learning Engineer"]
    j = jobs[0]
    assert j.workplace_type == "hybrid" and j.remote is False and j.employment_type == "FullTime"
    assert j.locations == ["San Francisco", "New York"] and j.posted_at == "2026-11-05"
    assert j.salary["text"] == "$200K – $300K"
    req = [r for r in base.requests if r.path.startswith("/posting-api")][0]
    assert "includeCompensation=true" in req.path


async def test_ashby_get_job_from_list(base, client):
    base.route("/posting-api/job-board/acme", Response(js(ASHBY), headers=JSON))
    assert (await ashby.get_job(client, Board("ashby", "acme"), "c1")).title == "Machine Learning Engineer"
    assert await ashby.get_job(client, Board("ashby", "acme"), "c2") is None  # unlisted counts as closed
    assert await ashby.get_job(client, Board("ashby", "acme"), "zzz") is None


async def test_workday_pages_with_post(base, client):
    def handler(info):
        body = json.loads(info.body)
        return Response(js(workday_page(body["offset"], body["limit"])), headers=JSON)

    base.route("/wday/cxs/acme/External/jobs", handler)
    board = Board("workday", "acme", host="acme.wd5.myworkdayjobs.com", site="External")
    jobs, _ = await workday.list_jobs(client, board)
    assert len(jobs) == 45
    posts = [r for r in base.requests if r.path == "/wday/cxs/acme/External/jobs"]
    assert all(r.method == "POST" for r in posts) and len(posts) == 3
    assert json.loads(posts[0].body) == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    today = date.today()
    assert jobs[0].posted_at == today.isoformat()
    assert jobs[1].posted_at == (today - timedelta(days=1)).isoformat()
    assert jobs[2].posted_at == (today - timedelta(days=5)).isoformat()
    assert jobs[0].url == "https://acme.wd5.myworkdayjobs.com/External/job/India-Bangalore/Python-Engineer-0_JR00000"


async def test_workday_caps_at_200(base, client):
    base.route("/wday/cxs/acme/External/jobs",
               lambda info: Response(js(workday_page(json.loads(info.body)["offset"], 20, total=1000)), headers=JSON))
    jobs, _ = await workday.list_jobs(client, Board("workday", "acme", host="acme.wd5.myworkdayjobs.com", site="External"))
    assert len(jobs) == 200


async def test_workday_get_job_and_closed(base, client):
    path = "/job/India-Bangalore/Python-Engineer-0_JR00000"
    base.route("/wday/cxs/acme/External" + path, Response(js(WORKDAY_DETAIL), headers=JSON))
    board = Board("workday", "acme", host="acme.wd5.myworkdayjobs.com", site="External")
    j = await workday.get_job(client, board, path)
    assert j.title == "Python Engineer 0" and "Write Python" in j.description
    assert await workday.get_job(client, board, "/job/x/Gone_JR1") is None


async def test_recruitee_list(base, client):
    base.route("/api/offers/", Response(js(RECRUITEE), headers=JSON))
    jobs, _ = await recruitee.list_jobs(client, Board("recruitee", "acme", host="acme.recruitee.com"))
    j = jobs[0]
    assert (j.title, j.remote, j.department, j.posted_at) == ("Frontend Developer", True, "Product", "2026-11-12")
    assert j.url == "https://acme.recruitee.com/o/frontend-developer"


async def test_ashby_workplace_type_beats_is_remote(base, client):
    raw = dict(ASHBY["jobs"][0], isRemote=True, workplaceType="Hybrid")
    base.route("/posting-api/job-board/acme", Response(js({"jobs": [raw]}), headers=JSON))
    [j], _ = await ashby.list_jobs(client, Board("ashby", "acme"))
    assert j.remote is False and j.workplace_type == "hybrid"
