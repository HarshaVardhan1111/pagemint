import json

from conftest import Response
from pagemint import __version__
from pagemint.cli import main, slug_for

BASE = ["--allow-private", "--rate-limit", "0"]
PAGE = "<html><head><title>T</title></head><body><main><h1>Hello CLI</h1><p>Some body text.</p><a href='/two'>two</a></main></body></html>"


def test_scrape_prints_markdown(server, capsys):
    server.route("/", PAGE)
    assert main(["scrape", server.url("/"), *BASE]) == 0
    out = capsys.readouterr().out
    assert "# Hello CLI" in out


def test_scrape_json(server, capsys):
    server.route("/", PAGE)
    assert main(["scrape", server.url("/"), "--json", *BASE]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status_code"] == 200 and data["title"] == "T"


def test_scrape_links_format(server, capsys):
    server.route("/", PAGE)
    assert main(["scrape", server.url("/"), "--format", "links", *BASE]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [server.url("/two")]


def test_scrape_error_exit_code(capsys):
    assert main(["scrape", "ftp://example.com/"]) == 1
    err = capsys.readouterr().err
    assert "http" in err and "invalid_url" in err


def test_blocked_without_allow_private(server, capsys):
    server.route("/", PAGE)
    assert main(["scrape", server.url("/"), "--rate-limit", "0"]) == 1
    assert "private" in capsys.readouterr().err


def test_crawl_prints_pages(server, capsys):
    server.route("/", PAGE)
    server.route("/two", "<html><body><main><p>Second page</p></main></body></html>")
    assert main(["crawl", server.url("/"), "--max-pages", "2", *BASE]) == 0
    out = capsys.readouterr().out
    assert "Hello CLI" in out and "Second page" in out
    assert server.url("/two") in out


def test_crawl_out_dir_writes_files_without_caps(server, tmp_path, capsys):
    server.route("/", PAGE)
    server.route("/two", "<html><body><main><p>" + "long " * 6000 + "</p></main></body></html>")
    assert main(["crawl", server.url("/"), "--out", str(tmp_path), *BASE]) == 0
    files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert len(files) == 2
    two = (tmp_path / slug_for(server.url("/two"))).read_text()
    assert len(two) > 20_000  # no per-page cap when saving to disk
    assert "2 pages" in capsys.readouterr().err


def test_map_prints_urls(server, capsys):
    server.route("/", PAGE)
    assert main(["map", server.url("/"), *BASE]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert server.url("/two") in lines


def test_slug_for():
    assert slug_for("https://example.com/") == "example.com_index.md"
    assert slug_for("https://example.com/docs/a b?x=1") == "example.com_docs_a_b_x_1.md"


def test_version(capsys):
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    assert __version__ in capsys.readouterr().out


def test_serve_is_default(monkeypatch):
    called = {}
    monkeypatch.setattr("pagemint.cli.run_server", lambda settings: called.setdefault("ok", settings))
    assert main([]) == 0
    assert "ok" in called


def test_timeout_flag(server, capsys):
    server.route("/slow", Response("<p>x</p>", delay=1.0))
    assert main(["scrape", server.url("/slow"), "--timeout", "0.3", *BASE]) == 1
    assert "timeout" in capsys.readouterr().err


def test_options_before_subcommand(server, capsys):
    server.route("/", PAGE)
    assert main([*BASE, "scrape", server.url("/")]) == 0
    assert "# Hello CLI" in capsys.readouterr().out
