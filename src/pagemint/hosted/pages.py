"""Small public pages for hosted PageMint: home, terms, privacy, health."""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse, JSONResponse

from pagemint import __version__

REPO = "https://github.com/HarshaVardhan1111/pagemint"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        f"<title>PageMint – {html.escape(title)}</title></head>"
        "<body style='font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5'>"
        f"{body}<hr><p><a href='/'>Home</a> · <a href='/terms'>Terms</a> · <a href='/privacy'>Privacy</a> · "
        f"<a href='{REPO}'>Source code</a></p></body></html>"
    )


def home(public_url: str) -> HTMLResponse:
    mcp_url = html.escape(public_url.rstrip("/") + "/mcp")
    return _page("Free web pages and open jobs for Claude", f"""
<h1>PageMint</h1>
<p>A free, open-source MCP server that gives Claude web pages as clean Markdown, site crawls and maps,
open jobs straight from companies' hiring systems, and web search.</p>
<h2>Add it to Claude</h2>
<ol>
<li>On claude.ai (web or desktop app) open <b>Settings → Connectors</b> and choose <b>Add custom connector</b>.</li>
<li>Name: <code>PageMint</code>. URL: <code>{mcp_url}</code></li>
<li>Click <b>Connect</b> and sign in with GitHub.</li>
<li>On the phone, the connector appears once you have added it on the web.</li>
</ol>
<p>Daily limits per person: 200 pages, 100 job searches, 50 web searches, 30 JavaScript page renders.</p>""")


def terms() -> HTMLResponse:
    return _page("Terms", """
<h1>Terms of use</h1>
<ul>
<li>PageMint is provided free, as is, without any guarantee. It may be slow, limited or unavailable.</li>
<li>Use it only for lawful purposes and respect the websites you read. PageMint follows each site's robots.txt
and rate limits; do not try to get around them, overload sites, or reach private networks.</li>
<li>Accounts that abuse the service can be blocked. Limits can change.</li>
<li>Content you read through PageMint belongs to its owners.</li>
</ul>""")


def privacy() -> HTMLResponse:
    return _page("Privacy", """
<h1>Privacy</h1>
<ul>
<li>Sign-in uses GitHub. PageMint keeps your GitHub user id and username, nothing else from GitHub, and does not
keep your GitHub token.</li>
<li>Your saved company list and daily usage counts are stored on this server.</li>
<li>For each request PageMint logs: time, your user id, the tool, the website's host name, the result and the
duration. It does not log page contents, full links, or search words. Logs are deleted after 14 days.</li>
<li>Nothing is sold or shared. Contact and source code: the GitHub repository linked below.</li>
</ul>""")


def health() -> JSONResponse:
    return JSONResponse({"ok": True, "version": __version__})
