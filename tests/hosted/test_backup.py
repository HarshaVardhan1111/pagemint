import sqlite3
import tarfile

from pagemint.cli import main
from pagemint.hosted.store import HostedStore
from pagemint.jobs.model import Board
from pagemint.jobs.store import JobStore


def test_backup_command_copies_databases(tmp_path):
    data = tmp_path / "data"
    HostedStore(str(data / "hosted.sqlite3")).upsert_user(1, "alice")
    JobStore(str(data / "jobs.sqlite3")).save_company("Acme", Board("greenhouse", "acme"), "confirmed", user_id="u1")
    out = tmp_path / "backup.tar.gz"
    assert main(["admin", "backup", str(out), "--data-dir", str(data)]) == 0
    with tarfile.open(out) as t:
        names = sorted(t.getnames())
        t.extractall(tmp_path / "restore")
    assert names == ["hosted.sqlite3", "jobs.sqlite3"]
    conn = sqlite3.connect(tmp_path / "restore" / "hosted.sqlite3")
    assert conn.execute("SELECT login FROM users").fetchone()[0] == "alice"


def test_backup_with_no_data_is_an_error(tmp_path, capsys):
    assert main(["admin", "backup", str(tmp_path / "b.tar.gz"), "--data-dir", str(tmp_path / "empty")]) == 1
    assert "nothing to back up" in capsys.readouterr().err
