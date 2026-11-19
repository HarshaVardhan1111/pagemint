"""GitHub as the identity provider: who is signing in. Only the public profile is read."""

from __future__ import annotations

from pagemint.errors import FetchError, PageMintError

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"
SCOPE = "read:user"


async def exchange_code(client, settings, code: str, redirect_uri: str) -> str:
    """Trade GitHub's one-time code for a short-lived GitHub token (used once, never stored)."""
    try:
        status, data = await client.fetcher.fetch_json(
            TOKEN_URL, method="POST", check_robots=False, follow_redirects=False,
            headers={"Accept": "application/json"},
            json_body={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                       "code": code, "redirect_uri": redirect_uri},
        )
    except PageMintError as e:
        raise FetchError(f"Could not reach GitHub to finish sign-in ({e.type}).") from e
    token = data.get("access_token") if isinstance(data, dict) else None
    if status != 200 or not token:
        reason = data.get("error") if isinstance(data, dict) else f"HTTP {status}"
        raise FetchError(f"GitHub did not accept the sign-in ({reason}).")
    return str(token)


async def fetch_user(client, gh_token: str) -> dict:
    try:
        status, data = await client.fetcher.fetch_json(
            f"{API_URL}/user", check_robots=False, follow_redirects=False,
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"},
        )
    except PageMintError as e:
        raise FetchError(f"Could not read your GitHub profile ({e.type}).") from e
    if status != 200 or not isinstance(data, dict) or "id" not in data or "login" not in data:
        raise FetchError(f"Could not read your GitHub profile (HTTP {status}).")
    return {"id": int(data["id"]), "login": str(data["login"]), "created_at": data.get("created_at")}
