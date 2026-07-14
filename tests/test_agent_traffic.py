"""Phase B1 — agent traffic metering + per-(agent, path_kind) mode CRUD.

Covers:
  - record_agent_traffic UPSERT (counter increments, last_seen refreshes,
    first_seen sticks)
  - mode CRUD (default count_only, append-only history, latest wins)
  - dashboard endpoints (auth gates, list shape, mode flip validation)
"""

from __future__ import annotations

import pytest

from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _mk_admin(username: str = "admin"):
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )


def _mk_auditor(username: str = "auditor"):
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["auditor"],
        must_change_password=False,
    )


def _login(client, username: str):
    r = client.post(
        "/login",
        data={"username": username, "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


# ---------------------------------------------------------------------------
# ledger helpers — meter + mode
# ---------------------------------------------------------------------------


def test_record_agent_traffic_creates_row_on_first_request():
    ledger.record_agent_traffic("agent:a", "embedding")
    rows = ledger.list_agent_traffic(agent_id="agent:a")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent:a"
    assert rows[0]["path_kind"] == "embedding"
    assert rows[0]["count"] == 1
    # Default mode when no history row exists.
    assert rows[0]["mode"] == ledger.TRAFFIC_MODE_COUNT_ONLY


def test_record_agent_traffic_increments_on_repeat():
    for _ in range(5):
        ledger.record_agent_traffic("agent:b", "chat")
    rows = ledger.list_agent_traffic(agent_id="agent:b")
    assert len(rows) == 1
    assert rows[0]["count"] == 5


def test_record_agent_traffic_separates_path_kinds():
    ledger.record_agent_traffic("agent:c", "chat")
    ledger.record_agent_traffic("agent:c", "chat")
    ledger.record_agent_traffic("agent:c", "embedding")
    rows = ledger.list_agent_traffic(agent_id="agent:c")
    counts = {r["path_kind"]: r["count"] for r in rows}
    assert counts == {"chat": 2, "embedding": 1}


def test_set_mode_appends_history_and_latest_wins():
    ledger.record_agent_traffic("agent:d", "embedding")
    # Default
    assert ledger.get_agent_traffic_mode("agent:d", "embedding") == "count_only"

    ledger.set_agent_traffic_mode(
        "agent:d", "embedding", "full_logging", changed_by=None
    )
    assert ledger.get_agent_traffic_mode("agent:d", "embedding") == "full_logging"

    # Flip back — newest row must win regardless of order.
    ledger.set_agent_traffic_mode("agent:d", "embedding", "count_only", changed_by=None)
    assert ledger.get_agent_traffic_mode("agent:d", "embedding") == "count_only"

    # list_agent_traffic must reflect the latest mode too.
    rows = ledger.list_agent_traffic(agent_id="agent:d")
    assert rows[0]["mode"] == "count_only"


def test_set_mode_rejects_invalid_value():
    with pytest.raises(ValueError):
        ledger.set_agent_traffic_mode(
            "agent:e",
            "embedding",
            "logging_everything",
            changed_by=None,
        )


def test_list_agent_traffic_scopes_to_one_agent():
    ledger.record_agent_traffic("agent:f", "chat")
    ledger.record_agent_traffic("agent:g", "embedding")
    rows = ledger.list_agent_traffic(agent_id="agent:f")
    assert all(r["agent_id"] == "agent:f" for r in rows)


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------


def test_traffic_endpoint_requires_auth(client):
    # Need a user to exist before middleware will 401 (it would 303
    # /setup with zero users) — make one but don't log in.
    _mk_admin()
    resp = client.get("/api/agent-traffic")
    assert resp.status_code == 401


def test_traffic_endpoint_returns_inventory(client):
    _mk_admin()
    _login(client, "admin")
    ledger.record_agent_traffic("agent:h", "embedding")
    ledger.record_agent_traffic("agent:h", "embedding")
    ledger.record_agent_traffic("agent:h", "chat")

    body = client.get("/api/agent-traffic?agent_id=agent:h").json()
    by_kind = {r["path_kind"]: r for r in body["items"]}
    assert set(by_kind.keys()) == {"embedding", "chat"}
    assert by_kind["embedding"]["count"] == 2
    assert by_kind["embedding"]["mode"] == "count_only"
    # ISO timestamps present.
    assert by_kind["embedding"]["first_seen"]
    assert by_kind["embedding"]["last_seen"]


def test_traffic_endpoint_visible_to_auditor(client):
    # Auditors and admins both need to see what's flowing — visibility is
    # not admin-gated, only the mode-flip POST is.
    _mk_admin()  # required so middleware doesn't 303 to /setup
    _mk_auditor()
    _login(client, "auditor")
    ledger.record_agent_traffic("agent:i", "embedding")

    resp = client.get("/api/agent-traffic?agent_id=agent:i")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_mode_flip_requires_admin(client):
    _mk_admin()
    _mk_auditor()
    _login(client, "auditor")
    ledger.record_agent_traffic("agent:j", "embedding")

    resp = client.post(
        "/api/agent-traffic/agent:j/embedding/mode",
        json={"mode": "full_logging"},
    )
    assert resp.status_code == 403


def test_mode_flip_writes_history_and_returns_row(client):
    _mk_admin()
    _login(client, "admin")
    ledger.record_agent_traffic("agent:k", "embedding")

    resp = client.post(
        "/api/agent-traffic/agent:k/embedding/mode",
        json={"mode": "full_logging"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "agent:k"
    assert body["path_kind"] == "embedding"
    assert body["mode"] == "full_logging"

    # List endpoint reflects it.
    listing = client.get("/api/agent-traffic?agent_id=agent:k").json()
    assert listing["items"][0]["mode"] == "full_logging"


def test_mode_flip_rejects_unknown_mode(client):
    _mk_admin()
    _login(client, "admin")
    ledger.record_agent_traffic("agent:l", "embedding")

    resp = client.post(
        "/api/agent-traffic/agent:l/embedding/mode",
        json={"mode": "log_everything_ever"},
    )
    assert resp.status_code == 400


def test_mode_flip_rejects_missing_mode(client):
    _mk_admin()
    _login(client, "admin")
    resp = client.post(
        "/api/agent-traffic/agent:m/embedding/mode",
        json={"not_mode": "full_logging"},
    )
    assert resp.status_code == 400
