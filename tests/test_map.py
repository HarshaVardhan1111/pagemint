import gzip

from conftest import Response
from pagemint.sitemap import parse_sitemap

XML = {"Content-Type": "application/xml"}


def urlset(urls):
    items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


def test_parse_sitemap_urlset_and_index():
    urls, children = parse_sitemap(urlset(["http://h/a", " http://h/b "]).encode())
    assert urls == ["http://h/a", "http://h/b"] and children == []
    idx = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>http://h/s1.xml</loc></sitemap></sitemapindex>'
    assert parse_sitemap(idx.encode()) == ([], ["http://h/s1.xml"])


def test_parse_sitemap_gzip_and_garbage():
    assert parse_sitemap(gzip.compress(urlset(["http://h/z"]).encode()))[0] == ["http://h/z"]
    assert parse_sitemap(b"not xml at all") == ([], [])


async def test_map_from_robots_sitemap(server, client):
    server.route("/robots.txt", Response(f"User-agent: *\nAllow: /\nSitemap: {server.url('/sm.xml')}\n", headers={"Content-Type": "text/plain"}))
    server.route("/sm.xml", Response(urlset([server.url(f"/p{i}") for i in range(3)]), headers=XML))
    server.route("/", "<html><body><a href='/linked'>x</a></body></html>")
    r = await client.map_site(server.url("/"), limit=3)
    assert r["source"] == "sitemap"
    assert r["urls"] == [server.url(f"/p{i}") for i in range(3)]


async def test_map_default_sitemap_location(server, client):
    server.route("/sitemap.xml", Response(urlset([server.url("/a")]), headers=XML))
    server.route("/", "<html><body><a href='/b'>b</a></body></html>")
    r = await client.map_site(server.url("/"))
    assert r["source"] == "sitemap+links"
    assert r["urls"] == [server.url("/a"), server.url("/"), server.url("/b")]


async def test_map_sitemap_index_gzip(server, client):
    server.route("/sitemap.xml", Response(
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>{server.url("/s1.xml.gz")}</loc></sitemap></sitemapindex>',
        headers=XML))
    server.route("/s1.xml.gz", Response(gzip.compress(urlset([server.url("/deep")]).encode()), headers={"Content-Type": "application/gzip"}))
    r = await client.map_site(server.url("/"), limit=1)
    assert r["urls"] == [server.url("/deep")]


async def test_map_falls_back_to_links(server, client):
    server.route("/", "<html><body><a href='/one'>1</a><a href='/two#frag'>2</a><a href='http://elsewhere.example/x'>x</a></body></html>")
    r = await client.map_site(server.url("/"))
    assert r["source"] == "links"
    assert server.url("/one") in r["urls"] and server.url("/two") in r["urls"]
    assert not any("elsewhere" in u for u in r["urls"])


async def test_map_limit_and_cap(server, client):
    server.route("/sitemap.xml", Response(urlset([server.url(f"/p{i}") for i in range(50)]), headers=XML))
    r = await client.map_site(server.url("/"), limit=10)
    assert len(r["urls"]) == 10
    r2 = await client.map_site(server.url("/"), limit=99_999)
    assert any("5000" in w for w in r2["warnings"])


async def test_map_ignores_other_hosts_in_sitemap(server, client):
    server.route("/sitemap.xml", Response(urlset([server.url("/a"), "http://other.example/b"]), headers=XML))
    r = await client.map_site(server.url("/"), limit=1)
    assert r["urls"] == [server.url("/a")]
