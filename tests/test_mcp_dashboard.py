"""
Tests for the /api/mcp/servers* dashboard endpoints (M1).

These exercise the admin-gated CRUD wrapper around mcp_registry plus the
operator-supplied-token probe used to seed the per-server detail page
with tools/list before live agent traffic exists.

The probe upstream is mocked — no network leaves the process — and we
assert that the operator-supplied bearer is forwarded verbatim, mirroring
the test_mcp_proxy.py contract for agent traffic.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
import pytest

from kyde import auth, dashboard, ledger, mcp_registry

PASSWORD = "CorrectHorse!Battery9"


# ---------------------------------------------------------------------------
# fixtures / helpers
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
    # Bootstrap gate: an admin must exist before /login works for anyone.
    _seed_user("admin", ["admin"])
    _seed_user("viewer", ["viewer"])
    _login(client, "viewer")
    return client


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    mcp_registry.invalidate_cache()
    yield
    mcp_registry.invalidate_cache()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_requires_session(client):
    # No session — no admin even exists yet, but the unauth gate fires first.
    resp = client.get("/api/mcp/servers")
    assert resp.status_code == 401


def test_list_open_to_viewers(viewer_client):
    """Read access is for any authenticated user — only writes are admin-only."""
    resp = viewer_client.get("/api/mcp/servers")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_create_requires_admin(viewer_client):
    resp = viewer_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    assert resp.status_code == 403


def test_patch_requires_admin(viewer_client):
    resp = viewer_client.patch("/api/mcp/servers/svc", json={"enabled": False})
    assert resp.status_code == 403


def test_delete_requires_admin(viewer_client):
    resp = viewer_client.delete("/api/mcp/servers/svc")
    assert resp.status_code == 403


def test_probe_requires_admin(viewer_client):
    resp = viewer_client.post(
        "/api/mcp/servers/svc/probe-tools",
        json={"authorization": "Bearer x"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_create_persists_and_appears_in_list(admin_client):
    resp = admin_client.post(
        "/api/mcp/servers",
        json={"name": "github", "upstream_url": "https://api.gh.test/mcp"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "github"
    assert body["upstream_url"] == "https://api.gh.test/mcp"
    assert body["enabled"] is True
    assert body["created_at"]  # ISO string, not None

    listing = admin_client.get("/api/mcp/servers").json()
    assert [s["name"] for s in listing["items"]] == ["github"]


def test_create_rejects_duplicate_name(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "dup", "upstream_url": "https://a.test/mcp"},
    )
    resp = admin_client.post(
        "/api/mcp/servers",
        json={"name": "dup", "upstream_url": "https://b.test/mcp"},
    )
    assert resp.status_code == 409
    # Existing row must not have been overwritten.
    assert mcp_registry.get_server("dup")["upstream_url"] == "https://a.test/mcp"


def test_create_validates_name(admin_client):
    resp = admin_client.post(
        "/api/mcp/servers",
        json={"name": "Bad Name!", "upstream_url": "https://u.test/mcp"},
    )
    assert resp.status_code == 400
    assert "server name" in resp.json()["error"]


def test_create_validates_url(admin_client):
    resp = admin_client.post(
        "/api/mcp/servers",
        json={"name": "ok", "upstream_url": "ftp://no.test/mcp"},
    )
    assert resp.status_code == 400


def test_create_requires_name_and_url(admin_client):
    resp = admin_client.post("/api/mcp/servers", json={})
    assert resp.status_code == 400
    resp = admin_client.post(
        "/api/mcp/servers", json={"upstream_url": "https://u.test/mcp"}
    )
    assert resp.status_code == 400
    resp = admin_client.post("/api/mcp/servers", json={"name": "ok"})
    assert resp.status_code == 400


def test_patch_changes_url_and_enabled(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://a.test/mcp"},
    )
    resp = admin_client.patch(
        "/api/mcp/servers/svc",
        json={"upstream_url": "https://b.test/mcp", "enabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream_url"] == "https://b.test/mcp"
    assert body["enabled"] is False


def test_patch_partial_update_preserves_other_fields(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://a.test/mcp"},
    )
    resp = admin_client.patch("/api/mcp/servers/svc", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream_url"] == "https://a.test/mcp"
    assert body["enabled"] is False


def test_patch_404_for_unknown_server(admin_client):
    resp = admin_client.patch("/api/mcp/servers/nope", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_rejects_non_boolean_enabled(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://a.test/mcp"},
    )
    resp = admin_client.patch("/api/mcp/servers/svc", json={"enabled": "yes"})
    assert resp.status_code == 400


def test_delete_removes_server(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://a.test/mcp"},
    )
    resp = admin_client.delete("/api/mcp/servers/svc")
    assert resp.status_code == 204
    assert mcp_registry.get_server("svc") is None


def test_delete_404_for_unknown_server(admin_client):
    resp = admin_client.delete("/api/mcp/servers/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Probe — operator-supplied bearer, never persisted
# ---------------------------------------------------------------------------


class _FakeProbeClient:
    """Capture the outbound call so we can assert the bearer was forwarded."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Optional[dict] = None,
        raise_exc: Optional[Exception] = None,
    ):
        self.status_code = status_code
        self.json_body = json_body or {
            "jsonrpc": "2.0",
            "id": "probe",
            "result": {"tools": [{"name": "search"}, {"name": "fetch"}]},
        }
        self.raise_exc = raise_exc
        self.captured: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, *, headers, content):
        self.captured = {"url": url, "headers": dict(headers), "content": content}
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(
            status_code=self.status_code,
            json=self.json_body,
            headers={"content-type": "application/json"},
        )


def _install_fake(monkeypatch, fake: _FakeProbeClient) -> None:
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", lambda *a, **kw: fake)


def test_probe_forwards_bearer_and_returns_tools(admin_client, monkeypatch):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    fake = _FakeProbeClient()
    _install_fake(monkeypatch, fake)

    resp = admin_client.post(
        "/api/mcp/servers/svc/probe-tools",
        json={"authorization": "Bearer secret-xyz"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [t["name"] for t in body["result"]["tools"]] == ["search", "fetch"]

    # The whole point of the probe: bearer reaches the upstream unchanged.
    assert fake.captured["headers"]["Authorization"] == "Bearer secret-xyz"
    assert fake.captured["url"] == "https://up.test/mcp"


def test_probe_404_for_unknown_server(admin_client):
    resp = admin_client.post(
        "/api/mcp/servers/nope/probe-tools",
        json={"authorization": "Bearer x"},
    )
    assert resp.status_code == 404


def test_probe_requires_authorization_in_body(admin_client):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    resp = admin_client.post("/api/mcp/servers/svc/probe-tools", json={})
    assert resp.status_code == 400


def test_probe_surfaces_upstream_transport_error_as_502(admin_client, monkeypatch):
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    fake = _FakeProbeClient(raise_exc=httpx.ConnectError("dns"))
    _install_fake(monkeypatch, fake)

    resp = admin_client.post(
        "/api/mcp/servers/svc/probe-tools",
        json={"authorization": "Bearer x"},
    )
    assert resp.status_code == 502


def test_probe_propagates_upstream_jsonrpc_error_status(admin_client, monkeypatch):
    """If the upstream returns 401 with a JSON-RPC error body, surface both."""
    admin_client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    fake = _FakeProbeClient(
        status_code=401,
        json_body={
            "jsonrpc": "2.0",
            "id": "probe",
            "error": {"code": -32001, "message": "auth"},
        },
    )
    _install_fake(monkeypatch, fake)

    resp = admin_client.post(
        "/api/mcp/servers/svc/probe-tools",
        json={"authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == -32001


# ---------------------------------------------------------------------------
# M3 — Per-tool policy endpoints
# ---------------------------------------------------------------------------


def _create_server(client) -> dict:
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "svc", "upstream_url": "https://up.test/mcp"},
    )
    assert resp.status_code == 200
    return resp.json()


def test_policies_list_empty_for_fresh_server(admin_client):
    _create_server(admin_client)
    resp = admin_client.get("/api/mcp/servers/svc/policies")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_policies_list_returns_404_for_unknown_server(admin_client):
    resp = admin_client.get("/api/mcp/servers/missing/policies")
    assert resp.status_code == 404


def test_policies_list_requires_admin(viewer_client):
    resp = viewer_client.get("/api/mcp/servers/svc/policies")
    assert resp.status_code == 403


def test_policies_put_creates_row(admin_client):
    _create_server(admin_client)
    resp = admin_client.put(
        "/api/mcp/servers/svc/policies/*/dangerous",
        json={"decision": "deny", "reason": "pii"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "*"
    assert body["tool_name"] == "dangerous"
    assert body["decision"] == "deny"
    assert body["reason"] == "pii"

    # Round-trip: list now shows the row.
    listed = admin_client.get("/api/mcp/servers/svc/policies").json()
    assert len(listed["items"]) == 1


def test_policies_put_updates_existing_row(admin_client):
    _create_server(admin_client)
    admin_client.put(
        "/api/mcp/servers/svc/policies/*/dangerous",
        json={"decision": "deny", "reason": "v1"},
    )
    resp = admin_client.put(
        "/api/mcp/servers/svc/policies/*/dangerous",
        json={"decision": "allow", "reason": "reconsidered"},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"
    assert resp.json()["reason"] == "reconsidered"
    # Still exactly one row.
    listed = admin_client.get("/api/mcp/servers/svc/policies").json()
    assert len(listed["items"]) == 1


def test_policies_put_rejects_invalid_decision(admin_client):
    _create_server(admin_client)
    resp = admin_client.put(
        "/api/mcp/servers/svc/policies/*/tool",
        json={"decision": "maybe"},
    )
    assert resp.status_code == 400


def test_policies_put_404_for_unknown_server(admin_client):
    resp = admin_client.put(
        "/api/mcp/servers/missing/policies/*/tool",
        json={"decision": "deny"},
    )
    assert resp.status_code == 404


def test_policies_put_requires_admin(viewer_client):
    resp = viewer_client.put(
        "/api/mcp/servers/svc/policies/*/tool",
        json={"decision": "deny"},
    )
    assert resp.status_code == 403


def test_policies_delete_removes_row(admin_client):
    _create_server(admin_client)
    admin_client.put(
        "/api/mcp/servers/svc/policies/*/dangerous",
        json={"decision": "deny"},
    )
    resp = admin_client.delete("/api/mcp/servers/svc/policies/*/dangerous")
    assert resp.status_code == 204
    listed = admin_client.get("/api/mcp/servers/svc/policies").json()
    assert listed["items"] == []


def test_policies_delete_404_when_no_row(admin_client):
    _create_server(admin_client)
    resp = admin_client.delete("/api/mcp/servers/svc/policies/*/dangerous")
    assert resp.status_code == 404


def test_policies_delete_requires_admin(viewer_client):
    resp = viewer_client.delete("/api/mcp/servers/svc/policies/*/dangerous")
    assert resp.status_code == 403


def test_policies_put_persists_wildcards_literally(admin_client):
    """The literal '*' is meaningful for the policy ladder — it must be
    stored verbatim, not URL-decoded into something else by the routing
    layer."""
    _create_server(admin_client)
    resp = admin_client.put(
        "/api/mcp/servers/svc/policies/*/*",
        json={"decision": "deny", "reason": "lockdown"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "*"
    assert body["tool_name"] == "*"
