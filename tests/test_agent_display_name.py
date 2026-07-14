"""
Tests for the `agents` table + display_name endpoints (Item 3 in the
UI follow-up plan).
"""

from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _append(agent_id: str) -> ledger.LedgerEntry:
    return ledger.append(
        agent_id=agent_id,
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )


def _mk_user(username: str, roles: list[str]) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=roles,
        must_change_password=False,
    )


def _login(client, username: str) -> None:
    resp = client.post(
        "/login",
        data={"username": username, "password": _PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_agents_table_populated_by_trigger():
    _append("agent:trigger-test-1")

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agent_id, display_name, first_seen, last_seen"
                "  FROM agents WHERE agent_id = %s",
                ("agent:trigger-test-1",),
            )
            row = cur.fetchone()

    assert row is not None
    assert row["display_name"] is None
    assert row["first_seen"] == row["last_seen"]


def test_list_agents_returns_rollups():
    _append("agent:list-test-A")
    _append("agent:list-test-A")
    _append("agent:list-test-B")

    rows = ledger.list_agents()
    by_id = {r["agent_id"]: r for r in rows}
    assert "agent:list-test-A" in by_id
    assert by_id["agent:list-test-A"]["entry_count"] == 2


def test_set_agent_display_name_updates_and_clears():
    _append("agent:rename-test")
    assert ledger.set_agent_display_name("agent:rename-test", "CRM Coding Agent")

    rows = ledger.list_agents()
    target = next(r for r in rows if r["agent_id"] == "agent:rename-test")
    assert target["display_name"] == "CRM Coding Agent"

    # Empty string and None both clear the name (whitespace also normalized).
    assert ledger.set_agent_display_name("agent:rename-test", "   ")
    target = next(r for r in rows if r["agent_id"] == "agent:rename-test")
    rows = ledger.list_agents()
    target = next(r for r in rows if r["agent_id"] == "agent:rename-test")
    assert target["display_name"] is None


def test_set_agent_display_name_returns_false_for_unknown():
    assert not ledger.set_agent_display_name("agent:does-not-exist", "x")


def test_api_agents_list_returns_rows(client):
    _mk_user("admin", ["admin"])
    _login(client, "admin")
    _append("agent:api-test")

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert any(a["agent_id"] == "agent:api-test" for a in body)


def test_api_agents_list_requires_auth(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 401


def test_api_agents_patch_requires_admin(client):
    # An admin must exist or the middleware forces /setup for everyone;
    # we want to exercise the role gate, not the bootstrap gate.
    _mk_user("admin", ["admin"])
    _mk_user("viewer", ["viewer"])
    _login(client, "viewer")
    _append("agent:patch-test")

    resp = client.patch(
        "/api/agents/agent:patch-test",
        json={"display_name": "should not work"},
    )
    assert resp.status_code == 403


def test_api_agents_patch_sets_display_name(client):
    _mk_user("admin", ["admin"])
    _login(client, "admin")
    _append("agent:patch-test")

    resp = client.patch(
        "/api/agents/agent:patch-test",
        json={"display_name": "Inventory Bot"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Inventory Bot"

    # Confirm it persisted by re-listing.
    listing = client.get("/api/agents").json()
    target = next(a for a in listing if a["agent_id"] == "agent:patch-test")
    assert target["display_name"] == "Inventory Bot"


def test_api_agents_patch_clears_with_null(client):
    _mk_user("admin", ["admin"])
    _login(client, "admin")
    _append("agent:clear-test")
    client.patch("/api/agents/agent:clear-test", json={"display_name": "tmp"})

    resp = client.patch("/api/agents/agent:clear-test", json={"display_name": None})
    assert resp.status_code == 200
    listing = client.get("/api/agents").json()
    target = next(a for a in listing if a["agent_id"] == "agent:clear-test")
    assert target["display_name"] is None


def test_api_agents_patch_rejects_unknown_agent(client):
    _mk_user("admin", ["admin"])
    _login(client, "admin")

    resp = client.patch(
        "/api/agents/agent:never-existed",
        json={"display_name": "x"},
    )
    assert resp.status_code == 404
