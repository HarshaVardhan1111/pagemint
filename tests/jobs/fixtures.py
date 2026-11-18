"""Hand-written fake feeds, shaped like the real APIs checked on 2026-11-18."""

import json

GREENHOUSE = {
    "jobs": [
        {
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
            "id": 101, "internal_job_id": 9, "title": "Senior Python Engineer",
            "company_name": "Acme Corp", "updated_at": "2026-11-10T10:00:00-05:00",
            "first_published": "2026-11-01T09:00:00-05:00", "requisition_id": "R1", "language": "en",
            "application_deadline": None,
            "location": {"name": "Bengaluru, India"},
            "content": "&lt;p&gt;Build &lt;strong&gt;data&lt;/strong&gt; tools &amp;amp; APIs.&lt;/p&gt;",
            "departments": [{"id": 1, "name": "Engineering"}], "offices": [], "metadata": [],
        },
        {
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
            "id": 102, "title": "Account Executive", "company_name": "Acme Corp",
            "updated_at": "2026-10-01T10:00:00Z", "first_published": None,
            "location": {"name": "Remote - US"}, "content": "&lt;p&gt;Sell things.&lt;/p&gt;",
            "departments": [], "offices": [],
        },
    ],
    "meta": {"total": 2},
}
GREENHOUSE_JOB = dict(GREENHOUSE["jobs"][0], pay_input_ranges=[
    {"min_cents": 3000000, "max_cents": 4500000, "currency_type": "INR", "title": "Base"}])

LEVER = [
    {
        "id": "aaa-111", "text": "Backend Engineer (Go)",
        "categories": {"commitment": "Full-time", "location": "Berlin, Germany", "team": "Platform",
                       "department": "Engineering", "allLocations": ["Berlin, Germany", "Remote, Europe"]},
        "country": "DE", "workplaceType": "hybrid", "createdAt": 1786469891368,
        "descriptionPlain": "You will build Go services.", "description": "<div>You will build Go services.</div>",
        "hostedUrl": "https://jobs.lever.co/acme/aaa-111", "applyUrl": "https://jobs.lever.co/acme/aaa-111/apply",
        "salaryRange": {"currency": "EUR", "interval": "per-year-salary", "min": 70000, "max": 90000},
    },
    {
        "id": "bbb-222", "text": "Designer", "categories": {"commitment": "Contract", "location": "Remote"},
        "workplaceType": "remote", "createdAt": 1795039233000, "descriptionPlain": "Design.",
        "hostedUrl": "https://jobs.lever.co/acme/bbb-222", "applyUrl": "https://jobs.lever.co/acme/bbb-222/apply",
    },
]

ASHBY = {
    "jobs": [
        {
            "id": "c1", "title": "Machine Learning Engineer", "department": "Research", "team": "Research",
            "employmentType": "FullTime", "location": "San Francisco",
            "secondaryLocations": [{"location": "New York"}], "publishedAt": "2026-11-05T02:20:02.702+00:00",
            "isListed": True, "isRemote": False, "workplaceType": "Hybrid",
            "jobUrl": "https://jobs.ashbyhq.com/acme/c1", "applyUrl": "https://jobs.ashbyhq.com/acme/c1/application",
            "descriptionHtml": "<p>Train models.</p>", "descriptionPlain": "Train models.",
            "compensation": {"compensationTierSummary": "$200K – $300K", "scrapeableCompensationSalarySummary": "$200K - $300K"},
        },
        {"id": "c2", "title": "Hidden Role", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/acme/c2",
         "location": "SF", "publishedAt": "2026-11-01T00:00:00Z"},
    ],
    "apiVersion": "1",
}


def workday_page(offset, limit, total=45):
    posts = [
        {"title": f"Python Engineer {i}", "externalPath": f"/job/India-Bangalore/Python-Engineer-{i}_JR{i:05d}",
         "locationsText": "India, Bangalore", "postedOn": "Posted Today" if i % 3 == 0 else ("Posted Yesterday" if i % 3 == 1 else "Posted 5 Days Ago"),
         "bulletFields": [f"JR{i:05d}"]}
        for i in range(offset, min(offset + limit, total))
    ]
    return {"total": total, "jobPostings": posts, "facets": [], "userAuthenticated": False}


WORKDAY_DETAIL = {"jobPostingInfo": {
    "title": "Python Engineer 0", "jobDescription": "<p>Write Python.</p>", "location": "India, Bangalore",
    "startDate": "2026-11-18", "timeType": "Full time", "jobReqId": "JR00000", "canApply": True,
    "externalUrl": "https://acme.wd5.myworkdayjobs.com/External/job/India-Bangalore/Python-Engineer-0_JR00000",
}}

RECRUITEE = {"offers": [
    {"id": 7, "slug": "frontend-developer", "title": "Frontend Developer", "description": "<p>React work.</p>",
     "requirements": "<p>3 years</p>", "location": "Amsterdam, Netherlands", "city": "Amsterdam",
     "country": "Netherlands", "remote": True, "careers_url": "https://acme.recruitee.com/o/frontend-developer",
     "careers_apply_url": "https://acme.recruitee.com/o/frontend-developer/c/new",
     "published_at": "2026-11-12 10:00:00 UTC", "employment_type_code": "fulltime", "department": "Product"},
]}


def js(obj) -> str:
    return json.dumps(obj)
