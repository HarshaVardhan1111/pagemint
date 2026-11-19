"""Checks that hosted mode is configured safely before the server starts."""

from __future__ import annotations

from urllib.parse import urlsplit

from pagemint.config import Settings

MIN_SECRET_KEY_LEN = 32


def validate_hosted(settings: Settings) -> list[str]:
    problems: list[str] = []
    url = settings.public_url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if not parts or parts.scheme != "https" or not parts.hostname:
        problems.append("PAGEMINT_PUBLIC_URL must be the server's https:// address (e.g. https://1-2-3-4.sslip.io).")
    if not settings.github_client_id:
        problems.append("PAGEMINT_GITHUB_CLIENT_ID is missing (from your GitHub OAuth App).")
    if not settings.github_client_secret:
        problems.append("PAGEMINT_GITHUB_CLIENT_SECRET is missing (from your GitHub OAuth App).")
    if len(settings.secret_key) < MIN_SECRET_KEY_LEN:
        problems.append(f"PAGEMINT_SECRET_KEY must be a random value of at least {MIN_SECRET_KEY_LEN} characters.")
    return problems


def is_local_host(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")
