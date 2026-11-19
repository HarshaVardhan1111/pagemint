"""Keep API keys out of every message PageMint produces."""

from __future__ import annotations

from pagemint.config import Settings

MIN_SECRET_LEN = 4


def secrets(settings: Settings) -> list[str]:
    return [k for k in (settings.tavily_api_key, settings.brave_api_key) if k and len(k) >= MIN_SECRET_LEN]


def redact(text: str, settings: Settings) -> str:
    for key in secrets(settings):
        text = text.replace(key, "***")
    return text
