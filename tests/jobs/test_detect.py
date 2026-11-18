import pytest

from conftest import Response
from fixtures import ASHBY, GREENHOUSE, LEVER, js
from pagemint.jobs import detect
from pagemint.jobs.readers import ashby, greenhouse, lever

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def base(server, monkeypatch):
    root = server.url("").rstrip("/")
    monkeypatch.setattr(greenhouse, "BASE", root)
    monkeypatch.setattr(ashby, "BASE", root)
    monkeypatch.setattr(lever, "HOST_OVERRIDE", root)
    return server


def test_slug_candidates():
    assert detect.slug_candidates("Acme Technologies Inc.") == ["acme", "acmetechnologies", "acme-technologies"]
    assert detect.slug_candidates("Open AI") == ["openai", "open-ai"]
    assert detect.slug_candidates("Stripe") == ["stripe"]
    assert len(detect.slug_candidates("Big Data Analytics Labs Pvt Ltd")) <= 3


def test_names_match():
    assert detect.names_match("Acme", "Acme Corp")
    assert detect.names_match("open ai", "OpenAI")
    assert not detect.names_match("Notion", "Notional Finance Partners")


async def test_greenhouse_name_match_is_confirmed(base, client):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    d = await detect.detect_company(client, "Acme")
    assert (d.board.system, d.board.board, d.confidence, d.company_name) == ("greenhouse", "acme", "confirmed", "Acme Corp")
    assert len(d.jobs) == 2


async def test_lever_match_is_guessed_with_warning(base, client):
    base.route("/v0/postings/acme", Response(js(LEVER), headers=JSON))
    d = await detect.detect_company(client, "Acme")
    assert (d.board.system, d.confidence) == ("lever", "guessed")
    assert any("If this is not the company you meant" in w for w in d.warnings)


async def test_confirmed_beats_bigger_guess(base, client):
    small_gh = dict(GREENHOUSE, jobs=GREENHOUSE["jobs"][:1])
    base.route("/v1/boards/acme/jobs", Response(js(small_gh), headers=JSON))
    base.route("/posting-api/job-board/acme", Response(js(ASHBY), headers=JSON))
    base.route("/v0/postings/acme", Response(js(LEVER), headers=JSON))
    d = await detect.detect_company(client, "Acme")
    assert d.board.system == "greenhouse" and d.confidence == "confirmed"
    assert any("lever" in w.lower() for w in d.warnings)


async def test_greenhouse_other_company_name_is_only_guessed(base, client):
    other = {"jobs": [dict(GREENHOUSE["jobs"][0], company_name="Totally Different Co")], "meta": {"total": 1}}
    base.route("/v1/boards/acme/jobs", Response(js(other), headers=JSON))
    d = await detect.detect_company(client, "Acme")
    assert d.confidence == "guessed"


async def test_nothing_found(base, client):
    d = await detect.detect_company(client, "Nobody Here")
    assert d.board is None
    assert any("careers-page URL" in w and "Workday" in w for w in d.warnings)


async def test_url_of_known_system(base, client):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    d = await detect.detect_company(client, "https://boards.greenhouse.io/acme")
    assert (d.board.system, d.confidence, d.company_name) == ("greenhouse", "confirmed", "Acme Corp")


async def test_careers_page_embedding_greenhouse(base, client):
    base.route("/v1/boards/acme/jobs", Response(js(GREENHOUSE), headers=JSON))
    base.route("/careers", '<html><body><div id="grnhse_app"></div><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script></body></html>')
    d = await detect.detect_company(client, base.url("/careers"))
    assert (d.board.system, d.board.board, d.board.careers_url) == ("greenhouse", "acme", base.url("/careers"))


async def test_careers_page_without_known_system_uses_general_reader(base, client):
    base.route("/careers", "<html><body><a href='/careers/dev'>Dev</a></body></html>")
    d = await detect.detect_company(client, base.url("/careers"))
    assert (d.board.system, d.board.board, d.confidence) == ("jsonld", base.url("/careers"), "confirmed")
