"""
Tests for the M5 admin-action audit log.

Covers the audit_log helper (record + list_actions) and confirms each
admin-gated endpoint that mutates governance state writes the expected
row. Operational telemetry only — these rows are NOT part of the signed
chain of custody, just a relational forensic trail.
"""

from __future__ import annotations

import pytest

from kyde import audit_log, auth, ledger, mcp_registry

PASSWORD = "CorrectHorse!Battery9"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _seed_user(username: str, roles: list[str]) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=roles,
        must_change_password=False,
    )


def _login(client, username: str) -> None:
    resp = client.post(
        "/login",
        data={"username": username, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


@pytest.fixture
def admin_client(client):
    _seed_user("admin", ["admin"])
    _login(client, "admin")
    return client


@pytest.fixture
def viewer_client(client):
    _seed_user("admin", ["admin"])
    _seed_user("viewer", ["viewer"])
    _login(client, "viewer")
    return client


@pytest.fixture
def auditor_client(client):
    _seed_user("admin", ["admin"])
    _seed_user("audi", ["auditor"])
    _login(client, "audi")
    return client


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    mcp_registry.invalidate_cache()
    yield
    mcp_registry.invalidate_cache()


def _all_actions() -> list[dict]:
    return audit_log.list_actions(limit=500)["items"]


# ---------------------------------------------------------------------------
# audit_log primitives
# ---------------------------------------------------------------------------


def test_record_writes_row_visible_to_list_actions():
    audit_log.record(
        actor_id=None,
        actor_username="alice",
        action="mcp_server.create",
        resource_type="mcp_server",
        resource_id="svc",
        before=None,
        after={"name": "svc", "upstream_url": "https://up/x"},
    )
    rows = _all_actions()
    assert len(rows) == 1
    r = rows[0]
    assert r["actor_username"] == "alice"
    assert r["action"] == "mcp_server.create"
    assert r["resource_type"] == "mcp_server"
    assert r["resource_id"] == "svc"
    assert r["before"] is None
    assert r["after"]["name"] == "svc"
    assert r["created_at"] is not None


def test_record_failure_swallows_and_does_not_raise(monkeypatch):
    """An audit failure must NOT take down the surrounding operation."""

    class BoomConn:
        def __enter__(self):
            raise RuntimeError("db gone")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ledger, "_conn", lambda: BoomConn())
    audit_log.record(
        actor_id=None,
        actor_username="x",
        action="x",
        resource_type="x",
        resource_id="x",
    )  # no exception


def test_list_actions_filters_by_actor_action_and_resource_type():
    audit_log.record(
        actor_id=None,
        actor_username="a",
        action="mcp_server.create",
        resource_type="mcp_server",
        resource_id="one",
    )
    audit_log.record(
        actor_id=None,
        actor_username="b",
        action="mcp_server.delete",
        resource_type="mcp_server",
        resource_id="one",
    )
    audit_log.record(
        actor_id=None,
        actor_username="a",
        action="dlp_policy.toggle",
        resource_type="dlp_policy",
        resource_id="aws_key",
    )

    by_action = audit_log.list_actions(action="mcp_server.create")
    assert by_action["total"] == 1
    assert by_action["items"][0]["actor_username"] == "a"

    by_type = audit_log.list_actions(resource_type="dlp_policy")
    assert by_type["total"] == 1
    assert by_type["items"][0]["resource_id"] == "aws_key"


def test_list_actions_orders_newest_first_and_paginates():
    for i in range(5):
        audit_log.record(
            actor_id=None,
            actor_username="x",
            action="mcp_server.create",
            resource_type="mcp_server",
            resource_id=f"svc-{i}",
        )
    page1 = audit_log.list_actions(limit=2, offset=0)
    page2 = audit_log.list_actions(limit=2, offset=2)
    assert page1["total"] == 5
    assert [r["resource_id"] for r in page1["items"]] == ["svc-4", "svc-3"]
    assert [r["resource_id"] for r in page2["items"]] == ["svc-2", "svc-1"]


# ---------------------------------------------------------------------------
# MCP admin endpoints emit audit rows
# ---------------------------------------------------------------------------


def _create_server(client) -> dict:
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_mcp_server_create_writes_audit(admin_client):
    _create_server(admin_client)
    rows = _all_actions()
    creates = [r for r in rows if r["action"] == "mcp_server.create"]
    assert len(creates) == 1
    r = creates[0]
    assert r["actor_username"] == "admin"
    assert r["resource_id"] == "svc"
    assert r["before"] is None
    assert r["after"]["name"] == "svc"


def test_mcp_server_update_writes_audit_with_before_and_after(admin_client):
    _create_server(admin_client)
    resp = admin_client.patch("/api/mcp/servers/svc", json={"enabled": False})
    assert resp.status_code == 200
    updates = [r for r in _all_actions() if r["action"] == "mcp_server.update"]
    assert len(updates) == 1
    r = updates[0]
    assert r["before"]["enabled"] is True
    assert r["after"]["enabled"] is False


def test_mcp_server_delete_writes_audit(admin_client):
    _create_server(admin_client)
    resp = admin_client.delete("/api/mcp/servers/svc")
    assert resp.status_code == 204
    deletes = [r for r in _all_actions() if r["action"] == "mcp_server.delete"]
    assert len(deletes) == 1
    r = deletes[0]
    assert r["before"]["name"] == "svc"
    assert r["after"] is None


def test_mcp_policy_set_and_delete_write_audit(admin_client):
    _create_server(admin_client)
    resp = admin_client.put(
        "/api/mcp/servers/svc/policies/*/tool_a",
        json={"decision": "deny", "reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    resp = admin_client.delete("/api/mcp/servers/svc/policies/*/tool_a")
    assert resp.status_code == 204

    actions = [r["action"] for r in _all_actions()]
    assert "mcp_policy.set" in actions
    assert "mcp_policy.delete" in actions

    sets = [r for r in _all_actions() if r["action"] == "mcp_policy.set"]
    assert sets[0]["resource_id"] == "svc/*/tool_a"
    assert sets[0]["after"]["decision"] == "deny"

    dels = [r for r in _all_actions() if r["action"] == "mcp_policy.delete"]
    assert dels[0]["before"]["decision"] == "deny"


# ---------------------------------------------------------------------------
# DLP policy endpoints emit audit rows
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_dlp_push(monkeypatch):
    """dlp_policies.set_enabled and push_active_set normally fan out to the
    dlp-regex container. Stub both to no-ops so the test focuses on the
    audit row, not on a live HTTP call."""
    from kyde import dlp_policies

    async def _ok(*a, **k):
        return {"ok": True}

    async def _set_ok(pattern_id, enabled, user_id):
        return {"pattern_id": pattern_id, "enabled": bool(enabled)}

    monkeypatch.setattr(dlp_policies, "push_active_set", _ok)
    monkeypatch.setattr(dlp_policies, "set_enabled", _set_ok)


def test_dlp_policy_toggle_writes_audit(admin_client, _stub_dlp_push):
    resp = admin_client.patch(
        "/api/dlp-policies/aws_access_key", json={"enabled": False}
    )
    assert resp.status_code == 200, resp.text
    toggles = [r for r in _all_actions() if r["action"] == "dlp_policy.toggle"]
    assert len(toggles) == 1
    assert toggles[0]["resource_id"] == "aws_access_key"
    assert toggles[0]["after"]["enabled"] is False


def test_dlp_policy_resync_writes_audit(admin_client, _stub_dlp_push):
    resp = admin_client.post("/api/dlp-policies/resync")
    assert resp.status_code == 200, resp.text
    rows = [r for r in _all_actions() if r["action"] == "dlp_policy.resync"]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# /api/audit-log endpoint
# ---------------------------------------------------------------------------


def test_audit_log_endpoint_requires_admin(viewer_client):
    assert viewer_client.get("/api/audit-log").status_code == 403


def test_audit_log_endpoint_unauth_returns_401(client):
    assert client.get("/api/audit-log").status_code == 401


def test_audit_log_endpoint_returns_actions(admin_client):
    _create_server(admin_client)
    resp = admin_client.get("/api/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(r["action"] == "mcp_server.create" for r in body["items"])


def test_audit_log_endpoint_filter_passes_through(admin_client):
    _create_server(admin_client)
    admin_client.delete("/api/mcp/servers/svc")
    resp = admin_client.get("/api/audit-log", params={"action": "mcp_server.delete"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "mcp_server.delete"
