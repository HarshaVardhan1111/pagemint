import threading
import time

from pagemint.hosted.store import HostedStore


def test_users(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    uid = s.upsert_user(1234, "alice")
    assert s.upsert_user(1234, "alice-renamed") == uid
    assert s.get_user(uid)["login"] == "alice-renamed"
    assert s.upsert_user(999, "bob") != uid


def test_clients(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    s.save_client("c1", {"client_id": "c1", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
    assert s.get_client("c1")["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert s.get_client("nope") is None


def test_codes_single_use_and_expiry(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    s.put_code("code-1", {"x": 1}, expires_at=time.time() + 60)
    assert s.take_code("code-1") == {"x": 1}
    assert s.take_code("code-1") is None
    s.put_code("code-2", {"x": 2}, expires_at=time.time() - 1)
    assert s.take_code("code-2") is None


def test_tokens_revoke_and_expiry(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    s.put_token("acc-1", "access", {"subject": "u1", "client_id": "c1"}, expires_at=time.time() + 60)
    s.put_token("ref-1", "refresh", {"subject": "u1", "client_id": "c1"}, expires_at=time.time() + 60)
    assert s.get_token("acc-1", "access")["subject"] == "u1"
    assert s.get_token("acc-1", "refresh") is None  # kind must match
    s.revoke_token("acc-1")
    assert s.get_token("acc-1", "access") is None
    s.put_token("old", "access", {"subject": "u1", "client_id": "c1"}, expires_at=time.time() - 1)
    assert s.get_token("old", "access") is None
    s.revoke_all_for("u1", "c1")
    assert s.get_token("ref-1", "refresh") is None


def test_pending(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    s.put_pending("state-1", {"client_id": "c1"}, expires_at=time.time() + 60)
    assert s.take_pending("state-1") == {"client_id": "c1"}
    assert s.take_pending("state-1") is None


def test_secrets_never_stored_in_plain_text(tmp_path):
    path = tmp_path / "h.db"
    s = HostedStore(str(path))
    s.put_token("PLAIN-ACCESS-TOKEN-VALUE", "access", {"subject": "u"}, expires_at=time.time() + 60)
    s.put_code("PLAIN-AUTH-CODE-VALUE", {"subject": "u"}, expires_at=time.time() + 60)
    s.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert b"PLAIN-ACCESS-TOKEN-VALUE" not in raw and b"PLAIN-AUTH-CODE-VALUE" not in raw


def test_usage_atomic(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))

    def work():
        for _ in range(50):
            s.add_usage("u1", "2026-11-19", "pages", 1)

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.get_usage("u1", "2026-11-19", "pages") == 500
    assert s.get_usage("u1", "2026-11-20", "pages") == 0
    assert s.add_usage("u1", "2026-11-19", "pages", 5) == 505


def test_purge_expired(tmp_path):
    s = HostedStore(str(tmp_path / "h.db"))
    s.put_code("c", {}, expires_at=time.time() - 1)
    s.put_pending("p", {}, expires_at=time.time() - 1)
    s.put_token("t", "access", {}, expires_at=time.time() - 1)
    assert s.purge_expired() == 3
