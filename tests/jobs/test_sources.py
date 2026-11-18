import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import Response, make_settings
from fixtures import js
from pagemint.client import PageMint
from pagemint.errors import FetchError
from pagemint.jobs.model import Job
from pagemint.jobs.rank import filter_and_rank
from pagemint.jobs.sources import SOURCES, arbeitnow, hn, himalayas

JSON = {"Content-Type": "application/json"}
NOW = datetime.now(timezone.utc)


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def base(server, monkeypatch):
    root = server.url("").rstrip("/")
    for mod in (hn, arbeitnow, himalayas):
        monkeypatch.setattr(mod, "BASE", root)
    return server


def test_registry():
    assert set(SOURCES) == {"hn", "arbeitnow", "himalayas"}


@pytest.mark.parametrize("first_line,title,company,location,remote,salary", [
    ("Simple Analytics | DevOps Engineer | REMOTE (worldwide) | Full-time | €5,000&#x2F;month",
     "DevOps Engineer", "Simple Analytics", "REMOTE (worldwide)", True, "€5,000/month"),
    ("Acme | Bangalore, India | Senior Backend Developer | ONSITE",
     "Senior Backend Developer", "Acme", "Bangalore, India", None, None),
    ("We are hiring lots of people for many teams in our growing company and this line has no pipes at all, really long text here",
     None, None, "", None, None),
])
def test_hn_first_line_parsing(first_line, title, company, location, remote, salary):
    p = hn.parse_post(f"{first_line}<p>More details. Apply: <a href=\"https:&#x2F;&#x2F;acme.example&#x2F;jobs\">link</a>", 123, iso(1))
    if title is None:
        assert p.title.startswith("We are hiring lots") and len(p.title) <= 120
    else:
        assert (p.title, p.company, p.location, p.remote) == (title, company, location, remote)
        assert (p.salary or {}).get("text") == salary
    assert p.url == "https://acme.example/jobs" and p.status == "unverified" and p.source == "hn"


def test_hn_post_without_link_uses_item_url():
    p = hn.parse_post("Acme | Engineer | Remote", 777, iso(1))
    assert p.url == "https://news.ycombinator.com/item?id=777"


async def test_hn_fetch_current_and_previous_month(base, client):
    base.route("/api/v1/search_by_date", Response(js({"hits": [
        {"objectID": "3", "title": "Ask HN: Who wants to be hired? (November 2026)", "created_at": iso(16)},
        {"objectID": "2", "title": "Ask HN: Who is hiring? (November 2026)", "created_at": iso(16)},
        {"objectID": "1", "title": "Ask HN: Who is hiring? (October 2026)", "created_at": iso(48)},
        {"objectID": "0", "title": "Ask HN: Who is hiring? (September 2026)", "created_at": iso(78)},
    ]}), headers=JSON))
    base.route("/api/v1/items/2", Response(js({"id": 2, "children": [
        {"id": 21, "text": "Acme | Python Engineer | Remote", "created_at": iso(10)},
        {"id": 22, "text": None, "created_at": iso(10)},  # deleted comment
    ]}), headers=JSON))
    base.route("/api/v1/items/1", Response(js({"id": 1, "children": [
        {"id": 11, "text": "Beta | Designer | Berlin", "created_at": iso(40)},
        {"id": 12, "text": "Gamma | Old Job | NYC", "created_at": iso(50)},  # older than 45 days
    ]}), headers=JSON))
    jobs = await hn.fetch(client, "python")
    assert sorted(j.title for j in jobs) == ["Designer", "Python Engineer"]
    assert base.hits("/api/v1/items/0") == 0 and base.hits("/api/v1/items/3") == 0


async def test_arbeitnow_pages(base, client):
    def handler(info):
        page = int(info.path.split("page=")[1]) if "page=" in info.path else 1
        data = [{"slug": f"s{page}", "company_name": "Acme GmbH", "title": f"Entwickler {page}", "description": "<p>Code</p>",
                 "remote": page == 2, "url": f"https://www.arbeitnow.com/jobs/s{page}", "tags": ["IT"],
                 "job_types": ["Full Time"], "location": "Berlin", "created_at": 1795039233}]
        nxt = f"/api/job-board-api?page={page + 1}" if page < 2 else None
        return Response(js({"data": data, "links": {"next": nxt}, "meta": {}}), headers=JSON)

    base.route("/api/job-board-api", handler)
    jobs = await arbeitnow.fetch(client, "")
    assert [j.title for j in jobs] == ["Entwickler 1", "Entwickler 2"]
    assert jobs[1].remote is True and jobs[0].employment_type == "Full Time" and jobs[0].posted_at == "2026-11-18"
    assert jobs[0].company == "Acme GmbH" and jobs[0].status == "unverified"


async def test_himalayas_search_and_expiry(base, client):
    future = int(time.time()) + 86400 * 30
    past = int(time.time()) - 86400
    base.route("/jobs/api/search", Response(js({"jobs": [
        {"title": "Python Developer", "companyName": "Remote Co", "employmentType": "Full Time", "minSalary": 50,
         "maxSalary": 70, "salaryPeriod": "hourly", "currency": "USD", "locationRestrictions": ["India", "Singapore"],
         "parentCategories": ["Engineering"], "description": "<p>Python</p>", "pubDate": 1795043132, "expiryDate": future,
         "applicationLink": "https://himalayas.app/companies/remote-co/jobs/python-developer", "guid": "g1"},
        {"title": "Expired", "companyName": "X", "pubDate": 1795043132, "expiryDate": past, "applicationLink": "https://h/x"},
    ]}), headers=JSON))
    jobs = await himalayas.fetch(client, "python")
    [j] = jobs
    assert (j.remote, j.location, j.department) == (True, "India, Singapore", "Engineering")
    assert j.salary == {"min": 50, "max": 70, "currency": "USD", "period": "hour", "text": None}
    req = [r for r in base.requests if r.path.startswith("/jobs/api/search")][0]
    assert "q=python" in req.path


async def test_himalayas_rate_limited(base, client):
    base.route("/jobs/api/search", Response("{}", status=429, headers=JSON))
    with pytest.raises(FetchError, match="429"):
        await himalayas.fetch(client, "python")


def test_large_feed_respects_limit_and_snippets():
    jobs = [Job(title=f"Python Engineer {i}", company="Big", url=f"https://x/{i}", source="ashby",
                description="<p>" + "lots of text " * 200 + "</p>", posted_at="2026-11-01") for i in range(834)]
    ranked = filter_and_rank(jobs, "python")[:25]
    out = [j.to_dict() for j in ranked]
    assert len(out) == 25 and all(len(d["description"]) <= 300 for d in out)


def test_hn_prefers_job_links_over_other_links():
    text = ('Evaboot | Python Engineer | Remote<p>Watch <a href="https:&#x2F;&#x2F;www.youtube.com&#x2F;watch?v=x">our video</a>. '
            'Apply: <a href="https:&#x2F;&#x2F;jobs.lever.co&#x2F;evaboot&#x2F;123">here</a>')
    assert hn.parse_post(text, 1, iso(1)).url == "https://jobs.lever.co/evaboot/123"
    text2 = 'Acme | Engineer | Remote<p><a href="https:&#x2F;&#x2F;acme.example&#x2F;careers">Careers</a> <a href="https:&#x2F;&#x2F;acme.example&#x2F;blog">blog</a>'
    assert hn.parse_post(text2, 2, iso(1)).url == "https://acme.example/careers"
