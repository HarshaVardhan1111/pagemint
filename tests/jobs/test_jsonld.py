import json
import time

from conftest import Response
from pagemint.jobs.jsonld import parse_jobpostings, read_careers_page


def page(*blocks, body=""):
    scripts = "".join(f'<script type="application/ld+json">{b if isinstance(b, str) else json.dumps(b)}</script>' for b in blocks)
    return f"<html><head>{scripts}</head><body>{body}</body></html>"


POSTING = {
    "@context": "https://schema.org", "@type": "JobPosting", "title": "Data Analyst",
    "hiringOrganization": {"@type": "Organization", "name": "Acme"},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Pune",
                                                   "addressRegion": "MH", "addressCountry": "IN"}},
    "employmentType": "FULL_TIME", "datePosted": "2026-11-10", "validThrough": "2027-01-01T00:00:00",
    "description": "<p>Analyse data.</p>", "url": "https://acme.example/jobs/1",
    "baseSalary": {"@type": "MonetaryAmount", "currency": "INR",
                   "value": {"@type": "QuantitativeValue", "minValue": 800000, "maxValue": 1200000, "unitText": "YEAR"}},
}


def test_single_posting_fields():
    [j] = parse_jobpostings(page(POSTING), "https://acme.example/jobs/1")
    assert (j.title, j.company, j.location, j.employment_type, j.posted_at, j.valid_through, j.source) == (
        "Data Analyst", "Acme", "Pune, MH, IN", "FULL_TIME", "2026-11-10", "2027-01-01", "jsonld")
    assert j.salary == {"min": 800000, "max": 1200000, "currency": "INR", "period": "year", "text": None}
    assert j.url == "https://acme.example/jobs/1"


def test_graph_list_and_type_list():
    other = dict(POSTING, title="Engineer", **{"@type": ["JobPosting", "Thing"]})
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, POSTING]}
    out = parse_jobpostings(page(graph, [other, {"@type": "Organization"}]), "https://acme.example/")
    assert sorted(j.title for j in out) == ["Data Analyst", "Engineer"]


def test_remote_string_org_and_plain_salary():
    p = dict(POSTING, jobLocationType="TELECOMMUTE", hiringOrganization="Acme Ltd",
             baseSalary={"@type": "MonetaryAmount", "currency": "USD", "value": 50, "unitText": "HOUR"})
    p.pop("jobLocation")
    [j] = parse_jobpostings(page(p), "https://acme.example/x")
    assert j.remote is True and j.company == "Acme Ltd"
    assert j.salary == {"min": 50, "max": 50, "currency": "USD", "period": "hour", "text": None}


def test_expired_dropped_and_malformed_ignored():
    expired = dict(POSTING, title="Old", validThrough="2020-01-01")
    out = parse_jobpostings(page("{not json", expired, POSTING), "https://acme.example/")
    assert [j.title for j in out] == ["Data Analyst"]


def test_url_defaults_to_page_and_html_entities():
    p = dict(POSTING, title="R&amp;D Lead")
    p.pop("url")
    [j] = parse_jobpostings(page(p), "https://acme.example/jobs/9")
    assert j.url == "https://acme.example/jobs/9" and j.title == "R&D Lead"


async def test_careers_page_follows_job_links(server, client):
    links = "".join(f'<a href="/careers/{n}">{n}</a>' for n in ("python-dev", "designer", "no-data")) + '<a href="/about">About</a>'
    server.route("/careers", page(body=links))
    server.route("/careers/python-dev", page(dict(POSTING, title="Python Dev", url=None)))
    server.route("/careers/designer", page(dict(POSTING, title="Designer", url=None)))
    server.route("/careers/no-data", "<html><body>Nothing here</body></html>")
    jobs = await read_careers_page(client, server.url("/careers"), deadline=time.monotonic() + 30)
    assert sorted(j.title for j in jobs) == ["Designer", "Python Dev"]
    assert server.hits("/about") == 0
    assert {j.url for j in jobs} == {server.url("/careers/python-dev"), server.url("/careers/designer")}


async def test_careers_page_with_postings_inline(server, client):
    server.route("/jobs", page(POSTING))
    assert [j.title for j in await read_careers_page(client, server.url("/jobs"), deadline=time.monotonic() + 30)] == ["Data Analyst"]


async def test_careers_page_deadline(server, client):
    links = "".join(f'<a href="/jobs/{i}">job {i}</a>' for i in range(10))
    server.route("/jobs", page(body=links))
    for i in range(10):
        server.route(f"/jobs/{i}", Response(page(dict(POSTING, title=f"J{i}")), delay=0.2))
    jobs = await read_careers_page(client, server.url("/jobs"), deadline=time.monotonic() + 0.5)
    assert 1 <= len(jobs) < 10
