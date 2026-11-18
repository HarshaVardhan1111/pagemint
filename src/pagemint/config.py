"""Settings for a PageMint client. Every limit lives here so it can be tuned."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

from pagemint import __version__

REPO_URL = "https://github.com/HarshaVardhan1111/pagemint"
DEFAULT_USER_AGENT = f"PageMint/{__version__} (+{REPO_URL})"

# Hard caps that callers cannot raise (spec: "Hard caps").
MAX_CRAWL_PAGES = 100
MAX_CRAWL_DEPTH = 5
MAX_MAP_LIMIT = 5000


@dataclass
class Settings:
    user_agent: str = DEFAULT_USER_AGENT
    rate_limit: float = 1.0  # seconds between requests to the same host
    connect_timeout: float = 10.0
    read_timeout: float = 20.0
    page_timeout: float = 30.0  # total time for one page (all redirects)
    max_bytes: int = 5_000_000
    max_redirects: int = 5
    allow_private_networks: bool = False
    js_fallback_chars: int = 200
    crawl_time_budget: float = 120.0
    page_char_cap: int = 20_000
    result_char_cap: int = 300_000
    robots_cache_ttl: float = 3600.0
    browser_executable: str = ""  # optional path to a Chrome/Chromium binary
    jobs_cache_hours: float = 6.0
    jobs_search_budget: float = 60.0
    data_dir: str = ""  # folder for the saved company list (default: the user's data folder)
    # Internal switch for tests; deliberately not settable from the environment.
    respect_robots: bool = field(default=True)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        """Build settings from defaults, then ``PAGEMINT_*`` env vars, then overrides."""
        values: dict = {}
        for f in fields(cls):
            if f.name == "respect_robots":
                continue
            raw = os.environ.get(f"PAGEMINT_{f.name.upper()}")
            if raw is None:
                continue
            values[f.name] = _parse(raw, type(getattr(cls(), f.name)))
        # Short alias documented in the README.
        if "PAGEMINT_ALLOW_PRIVATE" in os.environ:
            values["allow_private_networks"] = _parse(os.environ["PAGEMINT_ALLOW_PRIVATE"], bool)
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


def _parse(raw: str, typ: type):
    if typ is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if typ is int:
        return int(raw)
    if typ is float:
        return float(raw)
    return raw
