import pytest

from pagemint.jobs.detect import board_from_url, job_ref_from_url
from pagemint.jobs.model import Board


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme", Board("greenhouse", "acme")),
    ("https://job-boards.greenhouse.io/acme/jobs/123", Board("greenhouse", "acme")),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", Board("greenhouse", "acme")),
    ("https://jobs.lever.co/acme", Board("lever", "acme", host="api.lever.co")),
    ("https://jobs.eu.lever.co/acme/abc", Board("lever", "acme", host="api.eu.lever.co")),
    ("https://jobs.ashbyhq.com/acme", Board("ashby", "acme")),
    ("https://acme.wd5.myworkdayjobs.com/External", Board("workday", "acme", host="acme.wd5.myworkdayjobs.com", site="External")),
    ("https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/X/Y_JR1", Board("workday", "acme", host="acme.wd1.myworkdayjobs.com", site="Careers")),
    ("https://acme.recruitee.com/", Board("recruitee", "acme", host="acme.recruitee.com")),
    ("https://apply.workable.com/acme/", Board("workable", "acme", careers_url="https://apply.workable.com/acme/")),
])
def test_board_from_url(url, expected):
    got = board_from_url(url)
    if expected.system == "workable":
        assert (got.system, got.board) == ("workable", "acme")
    else:
        assert (got.system, got.board, got.host, got.site) == (expected.system, expected.board, expected.host, expected.site)


@pytest.mark.parametrize("url", ["https://example.com/careers", "https://boards.greenhouse.io/", "not a url"])
def test_board_from_url_none(url):
    assert board_from_url(url) is None


@pytest.mark.parametrize("url,system,job_id", [
    ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse", "123"),
    ("https://job-boards.greenhouse.io/acme/jobs/456?gh_src=x", "greenhouse", "456"),
    ("https://jobs.lever.co/acme/aaa-111", "lever", "aaa-111"),
    ("https://jobs.lever.co/acme/aaa-111/apply", "lever", "aaa-111"),
    ("https://jobs.ashbyhq.com/acme/c1", "ashby", "c1"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/India-Bangalore/Python_JR1", "workday", "/job/India-Bangalore/Python_JR1"),
    ("https://acme.recruitee.com/o/frontend-developer", "recruitee", "frontend-developer"),
])
def test_job_ref_from_url(url, system, job_id):
    board, jid = job_ref_from_url(url)
    assert board.system == system and jid == job_id


def test_job_ref_none_for_board_page():
    assert job_ref_from_url("https://jobs.lever.co/acme") is None
