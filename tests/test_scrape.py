from conftest import Response

PAGE = """<!doctype html>
<html><head><title>Guide Title</title></head>
<body>
<header><nav><a href="/home">Home nav link</a> <a href="/about">About nav</a></nav></header>
<div id="cookie-banner">We use cookies to improve things</div>
<main>
  <h1>Getting Started</h1>
  <p>PageMint turns pages into Markdown. Read the <a href="/docs/install">install guide</a>.</p>
  <table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>
  <pre><code class="language-python">print("hello")</code></pre>
  <p>More useful paragraph text so the main region is clearly the biggest block of content here.</p>
</main>
<footer>Copyright footer text</footer>
</body></html>"""


async def test_scrape_markdown_keeps_structure(server, client):
    server.route("/guide", PAGE)
    r = await client.scrape(server.url("/guide"))
    assert "error" not in r, r
    assert r["status_code"] == 200
    assert r["title"] == "Guide Title"
    md = r["content"]
    assert "# Getting Started" in md
    assert "| a | b |" in md
    assert "```python" in md and 'print("hello")' in md
    assert f"({server.url('/docs/install')})" in md
    for junk in ("Home nav link", "Copyright footer", "We use cookies"):
        assert junk not in md
    assert r["rendered_js"] is False
    assert server.url("/docs/install") in r["links"]


async def test_scrape_formats(server, client):
    server.route("/guide", PAGE)
    h = await client.scrape(server.url("/guide"), format="html")
    assert "<table" in h["content"] and "<nav" not in h["content"]
    links = await client.scrape(server.url("/guide"), format="links")
    assert links["content"] == ""
    assert all(u.startswith("http://") for u in links["links"])
    assert server.url("/home") in links["links"]


async def test_scrape_invalid_format(server, client):
    r = await client.scrape(server.url("/x"), format="pdf")
    assert r["error"]["type"] == "invalid_argument"


async def test_scrape_non_html_returns_error(server, client):
    server.route("/file.pdf", Response(b"%PDF-1.4", headers={"Content-Type": "application/pdf"}))
    r = await client.scrape(server.url("/file.pdf"))
    assert r["error"]["type"] == "content_type"
    assert "application/pdf" in r["error"]["message"]


async def test_scrape_404_returns_status_and_warning(server, client):
    server.route("/gone", Response("<html><body><main><p>Page not found here</p></main></body></html>", status=404))
    r = await client.scrape(server.url("/gone"))
    assert r["status_code"] == 404
    assert any("404" in w for w in r["warnings"])


async def test_decodes_meta_charset(server, client):
    html = '<html><head><meta charset="windows-1252"><title>t</title></head><body><main><p>Un café crème, s\'il vous plaît.</p></main></body></html>'
    server.route("/fr", Response(html.encode("cp1252"), headers={"Content-Type": "text/html"}))
    r = await client.scrape(server.url("/fr"))
    assert "café crème" in r["content"]


async def test_scrape_connection_refused_is_error_not_crash(client):
    r = await client.scrape("http://127.0.0.1:1/")
    assert r["error"]["type"] == "fetch"


async def test_scrape_plain_text(server, client):
    server.route("/notes.txt", Response("just some text", headers={"Content-Type": "text/plain"}))
    r = await client.scrape(server.url("/notes.txt"))
    assert r["content"].strip() == "just some text"
