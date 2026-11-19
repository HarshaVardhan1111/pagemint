"""Server-owner commands for hosted PageMint: block/unblock users and back up the data."""

from __future__ import annotations

import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

from pagemint.hosted.store import HostedStore

DATABASES = ("hosted.sqlite3", "jobs.sqlite3")


def set_blocked(data_dir: Path, login: str, blocked: bool) -> int:
    path = data_dir / "hosted.sqlite3"
    if not path.exists():
        print(f"pagemint: no hosted data in {data_dir}", file=sys.stderr)
        return 1
    if not HostedStore(str(path)).set_blocked(login, blocked):
        print(f"pagemint: no user with GitHub username '{login}' has signed in yet", file=sys.stderr)
        return 1
    print(f"pagemint: {login} {'blocked (all their sessions ended)' if blocked else 'unblocked'}")
    return 0


def backup(data_dir: Path, dest: Path, keep: int | None = None) -> int:
    found = [data_dir / n for n in DATABASES if (data_dir / n).exists()]
    if not found:
        print(f"pagemint: nothing to back up in {data_dir}", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for db in found:
            src = sqlite3.connect(str(db))
            out = sqlite3.connect(str(Path(tmp) / db.name))
            with out:
                src.backup(out)  # consistent copy even while PageMint is running
            src.close()
            out.close()
        with tarfile.open(dest, "w:gz") as tar:
            for db in found:
                tar.add(Path(tmp) / db.name, arcname=db.name)
    if keep:
        old = sorted(dest.parent.glob("pagemint-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]
        for p in old:
            p.unlink(missing_ok=True)
    print(f"pagemint: backup written to {dest}")
    return 0
