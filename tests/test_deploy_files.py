"""Sanity checks for the hosting files (the image itself is built in CI)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def load(p):
    return yaml.safe_load((ROOT / p).read_text())


def test_compose_services_and_profiles():
    c = load("deploy/docker-compose.yml")
    s = c["services"]
    assert set(s) == {"caddy", "pagemint", "pagemint-small", "searxng"}
    assert "profiles" not in s["caddy"] and s["caddy"]["ports"] == ["80:80", "443:443"]
    assert s["pagemint"]["profiles"] == ["full"] and s["searxng"]["profiles"] == ["full"]
    assert s["pagemint-small"]["profiles"] == ["small"]
    assert s["pagemint-small"]["networks"]["default"]["aliases"] == ["pagemint"]
    for name in ("pagemint", "pagemint-small"):
        assert "ports" not in s[name]  # only Caddy is reachable from outside
        assert s[name]["environment"]["PAGEMINT_HOSTED"] == "1"
    assert "ports" not in s["searxng"]
    assert s["pagemint"]["environment"]["PAGEMINT_SEARXNG_URL"] == "http://searxng:8080"
    assert s["pagemint-small"]["environment"]["PAGEMINT_JS_FALLBACK_CHARS"] == "0"


def test_caddy_and_searxng_config():
    caddy = (DEPLOY / "Caddyfile").read_text()
    assert "{$PAGEMINT_HOST}" in caddy and "reverse_proxy pagemint:8000" in caddy
    sx = load("deploy/searxng/settings.yml")
    assert "json" in sx["search"]["formats"]


def test_cloud_init():
    text = (DEPLOY / "cloud-init.yaml").read_text()
    assert text.startswith("#cloud-config")
    ci = yaml.safe_load(text)
    assert {"docker.io", "docker-compose-v2", "sqlite3"} <= set(ci["packages"])
    run = "\n".join(c if isinstance(c, str) else " ".join(c) for c in ci["runcmd"])
    assert "--dport 80" in run and "--dport 443" in run
    assert "openssl rand -hex 32" in run and "chown -R 10001:10001 /opt/pagemint/data" in run
    assert "usermod -aG docker deploy" in run


def test_env_example_has_no_real_secrets():
    env = (DEPLOY / ".env.example").read_text()
    for line in env.splitlines():
        if line.startswith(("PAGEMINT_GITHUB_CLIENT_SECRET=", "PAGEMINT_SECRET_KEY=", "SEARXNG_SECRET=")):
            assert "CHANGE-ME" in line


def test_deploy_workflow():
    wf = load(".github/workflows/deploy.yml")
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["image"]["permissions"]["packages"] == "write"
    assert "permissions" not in wf["jobs"]["server"]
    build = [st for st in wf["jobs"]["image"]["steps"] if "build-push-action" in st.get("uses", "")][0]
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    script = wf["jobs"]["server"]["steps"][-1]["run"]
    assert 'if [ -z "$DEPLOY_HOST" ]' in script and "exit 0" in script


def test_dockerfile():
    d = (ROOT / "Dockerfile").read_text()
    assert "USER pagemint" in d and "HEALTHCHECK" in d
    assert '"serve", "--http", "--host", "0.0.0.0"' in d


def test_cloud_init_gives_deploy_the_searxng_folder():
    ci = yaml.safe_load((DEPLOY / "cloud-init.yaml").read_text())
    run = "\n".join(c if isinstance(c, str) else " ".join(c) for c in ci["runcmd"])
    assert "chown -R deploy:deploy /opt/pagemint/searxng" in run


def test_backup_script_runs_inside_the_container():
    sh = (DEPLOY / "backup.sh").read_text()
    assert "docker compose exec -T" in sh and "pagemint admin backup" in sh
