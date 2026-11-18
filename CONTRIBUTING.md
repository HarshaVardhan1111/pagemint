# Contributing to PageMint

Thanks for helping. Bug reports, fixes and new test cases from real pages are all welcome.

## Setup

```bash
git clone https://github.com/HarshaVardhan1111/pagemint
```

```bash
cd pagemint
```

```bash
python -m venv .venv && . .venv/bin/activate
```

```bash
pip install -e ".[js,dev]"
```

```bash
playwright install chromium
```

```bash
pytest
```

The browser tests are skipped automatically if Chromium is not installed. If you already have Chrome or Chromium, point to it with `PAGEMINT_BROWSER_EXECUTABLE=/path/to/chrome`.

## Rules for changes

1. **Write it yourself.** Do not copy code from Firecrawl or other scrapers, even when their license would allow it. PageMint is written from scratch.
2. **Permissive dependencies only.** New dependencies must be MIT, BSD or Apache-2.0, and must be added to the license table in the README. Prefer the standard library.
3. **Safety rules are not optional.** Changes must keep robots.txt handling, per-site rate limits, size and time limits, and the SSRF checks (including on redirects and browser requests). Please do not add options that turn them off for normal users.
4. **Tests first, no internet.** Add a test that fails without your change. Tests use the local server in `tests/conftest.py`; they must never reach the live internet.
5. **Never crash the server.** Tools return `{"error": {"type", "message"}}` with a message a person can act on.

## Pull requests

- Keep each pull request to one change, with a clear description of why.
- Run `pytest` before pushing; CI runs it on Python 3.11, 3.12 and 3.13.
- For extraction changes, include the HTML that was handled badly (trimmed) as a test fixture.

## Reporting a page that converts badly

Open an issue with the URL, what you expected and what you got. If the page is private, attach a trimmed copy of its HTML instead.

## Security

If you find a way around the SSRF or robots.txt protections, please open a GitHub security advisory (Security tab → Report a vulnerability) instead of a public issue.
