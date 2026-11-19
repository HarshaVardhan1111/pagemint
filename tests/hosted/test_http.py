import json

from mcp import Client

from conftest import make_settings
from http_helpers import AppServer
from pagemint.cli import main
from pagemint.config import Settings
from pagemint.hosted.settings import validate_hosted
from pagemint.server import build_http_app


async def test_http_mode_serves_tools(server):
    server.route("/p", "<html><head><title>Over HTTP</title></head><body><main><p>hello</p></main></body></html>")
    app = build_http_app(make_settings())
    with AppServer(app) as srv:
        async with Client(f"{srv.url}/mcp") as c:
            names = {t.name for t in (await c.list_tools()).tools}
            res = await c.call_tool("scrape", {"url": server.url("/p")})
    assert {"scrape", "crawl", "map", "search_jobs", "search"} <= names
    assert json.loads(res.content[0].text)["title"] == "Over HTTP"


def test_refuses_public_bind_without_sign_in(capsys):
    assert main(["serve", "--http", "--host", "0.0.0.0", "--port", "8123"]) == 2
    assert "without sign-in" in capsys.readouterr().err


def test_hosted_refuses_to_start_with_bad_settings(capsys, monkeypatch):
    monkeypatch.setenv("PAGEMINT_HOSTED", "1")
    monkeypatch.setenv("PAGEMINT_PUBLIC_URL", "http://insecure.example")
    assert main(["serve", "--http", "--host", "0.0.0.0"]) == 2
    err = capsys.readouterr().err
    assert "https" in err and "PAGEMINT_GITHUB_CLIENT_ID" in err


def test_validate_hosted():
    ok = Settings(hosted=True, public_url="https://1-2-3-4.sslip.io", github_client_id="cid",
                  github_client_secret="csecret", secret_key="k" * 32)
    assert validate_hosted(ok) == []
    problems = validate_hosted(Settings(hosted=True, public_url="http://x", secret_key="short"))
    joined = " ".join(problems)
    assert "https" in joined and "PAGEMINT_GITHUB_CLIENT_ID" in joined and "PAGEMINT_GITHUB_CLIENT_SECRET" in joined
    assert "PAGEMINT_SECRET_KEY" in joined


def test_secrets_hidden_in_repr():
    s = Settings(github_client_secret="gh-super-secret", secret_key="signing-super-secret")
    assert "super-secret" not in repr(s)
