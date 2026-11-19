from datetime import date

from pagemint.jobs.model import Job, html_to_text, parse_date
from pagemint.jobs.rank import filter_and_rank

TODAY = date(2026, 11, 18)


def J(title, desc="", location="", remote=None, posted=None, dept=None, **kw):
    return Job(title=title, company="Acme", url=f"https://x/{title}", source="test", location=location,
               remote=remote, posted_at=posted, department=dept, description=desc, **kw)


def titles(jobs):
    return [j.title for j in jobs]


def test_title_match_beats_description_match():
    jobs = [J("Sales Lead", desc="we use python a lot"), J("Python Developer")]
    assert titles(filter_and_rank(jobs, "python", today=TODAY)) == ["Python Developer", "Sales Lead"]


def test_phrase_and_all_terms_bonus():
    jobs = [J("Engineer, Data Platform (Python)"), J("Python Engineer")]
    assert titles(filter_and_rank(jobs, "python engineer", today=TODAY))[0] == "Python Engineer"


def test_stop_words_ignored_and_zero_scores_dropped():
    jobs = [J("Python Engineer"), J("Chef")]
    assert titles(filter_and_rank(jobs, "engineer in python", today=TODAY)) == ["Python Engineer"]


def test_department_counts():
    jobs = [J("Engineer", dept="Data"), J("Engineer", dept="Sales")]
    assert filter_and_rank(jobs, "data engineer", today=TODAY)[0].department == "Data"


def test_location_alias():
    jobs = [J("A", location="Bengaluru, India"), J("B", location="Berlin")]
    assert titles(filter_and_rank(jobs, location="Bangalore", today=TODAY)) == ["A"]
    assert titles(filter_and_rank(jobs, location="bengaluru", today=TODAY)) == ["A"]


def test_remote_jobs_match_any_location_unless_excluded():
    jobs = [J("A", location="Berlin"), J("R", location="Anywhere", remote=True)]
    assert titles(filter_and_rank(jobs, location="Bangalore", today=TODAY)) == ["R"]
    assert titles(filter_and_rank(jobs, location="Bangalore", remote=False, today=TODAY)) == []
    assert titles(filter_and_rank(jobs, remote=True, today=TODAY)) == ["R"]
    assert titles(filter_and_rank(jobs, remote=False, today=TODAY)) == ["A"]


def test_posted_within_days_drops_old_and_undated():
    jobs = [J("New", posted="2026-11-15"), J("Old", posted="2026-09-01"), J("Undated")]
    assert titles(filter_and_rank(jobs, posted_within_days=7, today=TODAY)) == ["New"]


def test_recency_and_undated_sort_last():
    jobs = [J("Python Dev", posted=None), J("Python Dev", posted="2026-10-01"), J("Python Dev", posted="2026-11-17")]
    out = filter_and_rank(jobs, "python", today=TODAY)
    assert [j.posted_at for j in out] == ["2026-11-17", "2026-10-01", None]


def test_missing_fields_do_not_crash():
    assert filter_and_rank([Job(title="X", company="", url="u", source="s")], "x", location="y", today=TODAY) == []


def test_html_to_text_handles_escaped_html():
    assert html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;") == "Hi & bye"
    assert html_to_text("<p>One</p><p>Two&nbsp;three</p>") == "One Two three"


def test_parse_date():
    assert parse_date(1786469891368) == "2026-08-11"
    assert parse_date(1795039233) == "2026-11-18"
    assert parse_date("2026-11-05T02:20:02.702+00:00") == "2026-11-05"
    assert parse_date("2026-11-05") == "2026-11-05"
    assert parse_date("garbage") is None and parse_date(None) is None


def test_to_dict_snippet_and_full():
    j = J("T", desc="<p>" + "word " * 500 + "</p>")
    short = j.to_dict()
    assert len(short["description"]) <= 300 and "<p>" not in short["description"]
    full = j.to_dict(snippet=False)
    assert "word word" in full["description"] and len(full["description"]) <= 20_000
    assert {"title", "company", "url", "source", "status", "salary", "remote"} <= set(short)


def test_remote_job_limited_to_a_place_must_match_location():
    jobs = [J("A", location="Remote - US", remote=True), J("B", location="Remote (worldwide)", remote=True),
            J("C", location="Remote", remote=True), J("D", location="Remote-Friendly, United States", remote=True),
            J("E", location="Remote, UK", remote=True)]
    assert titles(filter_and_rank(jobs, location="London", today=TODAY)) == ["B", "C"]
    assert set(titles(filter_and_rank(jobs, location="UK", today=TODAY))) == {"B", "C", "E"}
    assert set(titles(filter_and_rank(jobs, location="USA", today=TODAY))) == {"A", "B", "C", "D"}


def test_full_description_unescapes_escaped_html():
    j = J("T", desc="&lt;p&gt;Hi &lt;strong&gt;there&lt;/strong&gt; &amp;amp; you&lt;/p&gt;")
    full = j.to_dict(snippet=False)["description"]
    assert "&lt;" not in full and "<p>" not in full and "**there**" in full and "& you" in full


def test_specific_word_required_and_generic_words_weigh_less():
    jobs = [
        J("Senior Software Engineer - Java Developer", desc="Java, Spring"),
        J("NetSuite Developer", desc="ERP work"),
        J("Senior Site Reliability Engineer (K8s, Terraform, Python)"),
        J("Python Developer"),
        J("Backend Engineer", desc="We use Python and Go"),
    ]
    assert titles(filter_and_rank(jobs, "python developer", today=TODAY)) == [
        "Python Developer", "Senior Site Reliability Engineer (K8s, Terraform, Python)", "Backend Engineer"]


def test_generic_only_query_still_works():
    jobs = [J("Senior Engineer"), J("Chef")]
    assert titles(filter_and_rank(jobs, "senior engineer", today=TODAY)) == ["Senior Engineer"]
