from conftest import Response, make_settings
from pagemint.client import PageMint


def page(title: str, links: list[str], extra: str = "") -> str:
    a = "".join(f'<li><a href="{h}">{h}</a></li>' for h in links)
    return f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1><p>Body text of {title}. {extra}</p><ul>{a}</ul></main></body></html>"


def chain_site(server, n: int):
    for i in range(n):
        server.route(f"/p{i}", page(f"P{i}", [f"/p{i + 1}"] if i + 1 < n else []))


async def test_crawl_respects_max_pages(server, client):
    for i in range(30):
        server.route(f"/p{i}", page(f"P{i}", [f"/p{j}" for j in range(30)]))
    r = await client.crawl(server.url("/p0"), max_pages=5, max_depth=5)
    assert len(r["pages"]) == 5
    assert r["pages"][0]["url"] == server.url("/p0")


async def test_crawl_respects_max_depth(server, client):
    chain_site(server, 6)
    r = await client.crawl(server.url("/p0"), max_pages=50, max_depth=2)
    assert {p["depth"] for p in r["pages"]} == {0, 1, 2}
    assert [p["title"] for p in r["pages"]] == ["P0", "P1", "P2"]


async def test_crawl_returns_markdown(server, client):
    chain_site(server, 2)
    r = await client.crawl(server.url("/p0"))
    assert "# P0" in r["pages"][0]["markdown"]
    assert r["pages"][0]["status_code"] == 200


async def test_crawl_same_domain(server, client):
    other = server.url("/p1", host="localhost")
    server.route("/p0", page("P0", [other, "/p2"]))
    server.route("/p1", page("P1", []))
    server.route("/p2", page("P2", []))
    r = await client.crawl(server.url("/p0"), same_domain=True)
    urls = [p["url"] for p in r["pages"]]
    assert other not in urls and server.url("/p2") in urls
    r2 = await client.crawl(server.url("/p0"), same_domain=False)
    assert other in [p["url"] for p in r2["pages"]]


async def test_crawl_dedupes_fragments(server, client):
    server.route("/p0", page("P0", ["/a", "/a#x", "/a?#y", "/b.pdf", "mailto:x@y.z"]))
    server.route("/a", page("A", []))
    r = await client.crawl(server.url("/p0"))
    urls = [p["url"] for p in r["pages"]]
    assert urls.count(server.url("/a")) == 1
    assert not any(u.endswith(".pdf") for u in urls)


async def test_crawl_clamps_caps(server, client):
    chain_site(server, 2)
    r = await client.crawl(server.url("/p0"), max_pages=500, max_depth=50)
    joined = " ".join(r["warnings"])
    assert "100" in joined and "5" in joined


async def test_crawl_page_char_cap(server, client):
    server.route("/big", page("Big", [], extra="word " * 12_000))
    r = await client.crawl(server.url("/big"))
    p = r["pages"][0]
    assert p["truncated"] is True
    assert len(p["markdown"]) <= 20_000


async def test_crawl_result_char_cap(server):
    for i in range(5):
        server.route(f"/p{i}", page(f"P{i}", [f"/p{j}" for j in range(5)], extra="x " * 3000))
    async with PageMint(make_settings(result_char_cap=10_000)) as c:
        r = await c.crawl(server.url("/p0"))
    assert r["stopped_early"] is True
    assert sum(len(p["markdown"]) for p in r["pages"]) <= 10_000
    assert any(s["reason"] == "output budget" for s in r["skipped"])


async def test_crawl_time_budget(server):
    for i in range(10):
        server.route(f"/p{i}", Response(page(f"P{i}", [f"/p{j}" for j in range(10)]), delay=0.2))
    async with PageMint(make_settings(crawl_time_budget=0.5)) as c:
        r = await c.crawl(server.url("/p0"), max_pages=10)
    assert r["stopped_early"] is True
    assert 1 <= len(r["pages"]) < 10
    assert any("time" in w for w in r["warnings"])


async def test_crawl_records_failed_pages(server, client):
    server.route("/p0", page("P0", ["/missing", "/file"]))
    server.route("/file", Response(b"%PDF", headers={"Content-Type": "application/pdf"}))
    r = await client.crawl(server.url("/p0"))
    reasons = {s["url"]: s["reason"] for s in r["skipped"]}
    assert "content type" in reasons[server.url("/file")].lower()
    # A 404 page is still returned (with its status), not skipped.
    assert any(p["status_code"] == 404 for p in r["pages"])


async def test_crawl_invalid_start_url(client):
    r = await client.crawl("notaurl")
    assert r["error"]["type"] == "invalid_url"
