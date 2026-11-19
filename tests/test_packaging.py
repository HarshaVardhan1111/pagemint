"""The MCP Registry entry (server.json) must agree with the package and its README."""

import json
from pathlib import Path

from pagemint import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_server_json_matches_package():
    data = json.loads((ROOT / "server.json").read_text())
    assert data["version"] == __version__
    [pkg] = data["packages"]
    assert (pkg["registryType"], pkg["identifier"], pkg["version"]) == ("pypi", "pagemint", __version__)
    assert pkg["transport"] == {"type": "stdio"}
    assert len(data["description"]) <= 100
    secrets = {e["name"]: e["isSecret"] for e in pkg["environmentVariables"]}
    assert secrets["PAGEMINT_TAVILY_API_KEY"] and secrets["PAGEMINT_BRAVE_API_KEY"]
    assert not any(e["isRequired"] for e in pkg["environmentVariables"])


def test_readme_has_registry_ownership_marker():
    name = json.loads((ROOT / "server.json").read_text())["name"]
    assert f"<!-- mcp-name: {name} -->" in (ROOT / "README.md").read_text()
