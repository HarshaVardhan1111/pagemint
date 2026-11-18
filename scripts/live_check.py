"""Try PageMint job search against real job feeds (needs internet; not part of the test suite).

    python scripts/live_check.py
    python scripts/live_check.py "data engineer" --location London --company Anthropic --company https://jobs.lever.co/palantir

Uses a temporary data folder, so your saved company list is not changed.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
import time

from pagemint.client import PageMint
from pagemint.jobs.service import JobService
from pagemint.jobs.store import JobStore

DEFAULT_COMPANIES = ["Anthropic", "https://jobs.lever.co/palantir", "https://jobs.ashbyhq.com/openai"]


async def main(query: str, location: str, companies: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        async with PageMint() as client:
            svc = JobService(client, JobStore(f"{tmp}/jobs.sqlite3"))
            for company in companies:
                t = time.monotonic()
                r = await svc.company_jobs(company, query=query, location=location, limit=3)
                if "error" in r:
                    print(f"[{company}] ERROR {r['error']['type']}: {r['error']['message']}")
                    continue
                c = r["company"]
                print(f"[{company}] {c['system']} ({c['confidence']}): {r['total_open']} open, "
                      f"{r['total_matches']} match, {time.monotonic() - t:.1f}s")
                for w in r["warnings"]:
                    print(f"    warning: {w}")
                for j in r["jobs"]:
                    print(f"    - {j['title']} | {j['location']} | {j['url']}")
            t = time.monotonic()
            r = await svc.search_jobs(query, location=location, limit=10)
            if "error" in r:
                print(f"[search] ERROR {r['error']['message']}")
                return
            print(f"[search] {r['total_matches']} matches in {time.monotonic() - t:.1f}s; skipped: {r['skipped']}")
            for j in r["jobs"]:
                print(f"    - {j['status']:<10} {j['source']:<10} {j['title'][:50]} | {j['company'][:25]} | {j['location'][:25]}")
            if r["jobs"]:
                ck = await svc.check_job(r["jobs"][0]["url"])
                print(f"[check_job] {ck.get('status')}: {ck.get('reason') or ck.get('error')}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", default="python engineer")
    p.add_argument("--location", default="")
    p.add_argument("--company", action="append", help="company name or careers URL (repeatable)")
    a = p.parse_args()
    asyncio.run(main(a.query, a.location, a.company or DEFAULT_COMPANIES))
