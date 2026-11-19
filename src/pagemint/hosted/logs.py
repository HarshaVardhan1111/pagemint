"""One line per hosted tool call: who, which tool, which site, result, time taken.

Never page contents, URL paths or query strings, or search words.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

KEEP_DAYS = 14


def host_of(target: str | None) -> str:
    if not target:
        return "-"
    try:
        return urlsplit(target).hostname or "-"
    except ValueError:
        return "-"


class CallLogger:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(f"pagemint.calls.{id(self)}")
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        self._handler = logging.handlers.TimedRotatingFileHandler(path, when="midnight", backupCount=KEEP_DAYS,
                                                                  utc=True, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)

    def log(self, user: str, tool: str, target: str | None, status: str, ms: int) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._logger.info(f"{ts} user={user} tool={tool} host={host_of(target)} status={status} ms={ms}")

    def close(self) -> None:
        self._logger.removeHandler(self._handler)
        self._handler.close()
