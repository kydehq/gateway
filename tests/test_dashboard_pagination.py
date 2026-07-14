"""
HTTP contract tests for the paginated dashboard endpoints:

- GET /api/entries     — cursor-paginated, filtered, searchable list
- GET /api/entries/facets — dropdown values for the Timeline filters
- GET /api/sessions     — paginated session summaries
- GET /api/sessions/{session_id} — entries for one session

These tests run against the FastAPI TestClient; they seed data through
`kyde.ledger.append` directly (no proxy/httpx roundtrip required).
"""

from __future__ import annotations

from typing import Any

from kyde import auth, ledger


PASSWORD = "CorrectHorse!Battery9"


def _seed_admin():
    return ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )


def _login(client) -> None:
    _seed_admin()
    resp = client.post(
        "/login",
        data={"username": "admin", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def _append(session_id: str = "", **overrides: Any) -> ledger.LedgerEntry:
    defaults = dict(
        agent_id="agent:test",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": []},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[],
        tool_calls=[],
        session_id=session_id,
    )
    defaults.update(overrides)
    return ledger.append(**defaults)


# ---------------------------------------------------------------------------
# /api/entries
# ---------------------------------------------------------------------------


def test_entries_endpoint_returns_pagination_envelope(client):
    _login(client)
    for i in range(5):
        _append(agent_id=f"agent:{i}")

    body = client.get("/api/entries?limit=3").json()
    assert set(body.keys()) >= {"items", "next_cursor", "has_more"}
    assert len(body["items"]) == 3
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


def test_entries_endpoint_cursor_walks_the_feed(client):
    _login(client)
    for i in range(7):
        _append(agent_id=f"agent:{i}")

    seen: list[int] = []
    cursor: int | None = None
    while True:
        url = "/api/entries?limit=3"
        if cursor is not None:
            url += f"&cursor={cursor}"
        page = client.get(url).json()
        seen.extend(e["seq"] for e in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    # Whole feed delivered exactly once, newest-first.
    assert seen == sorted(seen, reverse=True)
    assert set(seen) == set(range(1, 8))


def test_entries_endpoint_filter_by_action(client):
    _login(client)
    _append(action_type="chat")
    _append(action_type="tool_call")
    _append(action_type="chat")

    body = client.get("/api/entries?action=chat").json()
    assert len(body["items"]) == 2
    assert all(e["action_type"] == "chat" for e in body["items"])


def test_entries_endpoint_search(client):
    _login(client)
    _append(agent_id="agent:production-worker")
    _append(agent_id="agent:dev-worker")

    body = client.get("/api/entries?q=produ").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["agent_id"] == "agent:production-worker"


def test_entries_endpoint_combined_filter_and_search(client):
    _login(client)
    _append(action_type="chat", agent_id="agent:alpha", upstream="openai")
    _append(action_type="chat", agent_id="agent:bravo", upstream="openai")
    _append(action_type="tool_call", agent_id="agent:alpha", upstream="openai")

    body = client.get("/api/entries?action=chat&upstream=openai&q=alpha").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["agent_id"] == "agent:alpha"


def test_entries_endpoint_empty(client):
    _login(client)
    body = client.get("/api/entries").json()
    assert body == {
        "items": [],
        "next_cursor": None,
        "has_more": False,
        "total_count": 0,
    }


def test_entries_total_count_reflects_filtered_set(client):
    _login(client)
    _append(action_type="chat")
    _append(action_type="chat")
    _append(action_type="tool_call")

    # Unfiltered: 3 entries total
    body = client.get("/api/entries?limit=10").json()
    assert body["total_count"] == 3

    # Filtered by action — count drops, doesn't reflect the unfiltered total.
    body = client.get("/api/entries?limit=10&action=chat").json()
    assert body["total_count"] == 2
    assert len(body["items"]) == 2

    # Pagination doesn't reduce the count (cursor is excluded from the
    # COUNT query): a page-1 of size 1 still reports 3 unfiltered.
    body = client.get("/api/entries?limit=1").json()
    assert body["total_count"] == 3
    assert len(body["items"]) == 1
    assert body["has_more"] is True


def test_entries_items_include_derived_fields(client):
    """Timeline rows still need `dt` + tool helpers from the current UI."""
    _login(client)
    _append(tool_calls=[{"function": "read_file"}, {"function": "write_file"}])

    body = client.get("/api/entries?limit=1").json()
    e = body["items"][0]
    assert "dt" in e
    assert e["tool_count"] == 2
    assert e["first_tool"] == "read_file"


# ---------------------------------------------------------------------------
# /api/entries/facets
# ---------------------------------------------------------------------------


def test_entries_facets_empty(client):
    _login(client)
    body = client.get("/api/entries/facets").json()
    assert body == {"actions": [], "upstreams": []}


def test_entries_facets_distinct_values(client):
    _login(client)
    _append(action_type="chat", upstream="openai")
    _append(action_type="tool_call", upstream="anthropic")
    _append(action_type="chat", upstream="anthropic")

    body = client.get("/api/entries/facets").json()
    assert body["actions"] == ["chat", "tool_call"]
    assert body["upstreams"] == ["anthropic", "openai"]


# ---------------------------------------------------------------------------
# /api/sessions (summaries)
# ---------------------------------------------------------------------------


def test_sessions_endpoint_returns_pagination_envelope(client):
    _login(client)
    _append(session_id="s1")
    _append(session_id="s2")

    body = client.get("/api/sessions?limit=10").json()
    assert set(body.keys()) >= {"items", "next_cursor", "has_more"}
    ids = {s["session_id"] for s in body["items"]}
    assert ids == {"s1", "s2"}


def test_sessions_endpoint_aggregates(client):
    _login(client)
    _append(session_id="s1", agent_id="agent:a")
    _append(session_id="s1", agent_id="agent:b")
    _append(session_id="s1", agent_id="agent:a")

    body = client.get("/api/sessions").json()
    s1 = next(s for s in body["items"] if s["session_id"] == "s1")
    assert s1["entry_count"] == 3
    assert s1["agent_count"] == 2
    assert set(s1["agents"]) == {"agent:a", "agent:b"}


def test_sessions_endpoint_cursor_pagination(client):
    _login(client)
    _append(session_id="s1")
    _append(session_id="s2")
    _append(session_id="s3")

    page1 = client.get("/api/sessions?limit=2").json()
    assert [s["session_id"] for s in page1["items"]] == ["s3", "s2"]
    assert page1["has_more"] is True

    page2 = client.get(f"/api/sessions?limit=2&cursor={page1['next_cursor']}").json()
    assert [s["session_id"] for s in page2["items"]] == ["s1"]
    assert page2["has_more"] is False


# ---------------------------------------------------------------------------
# /api/sessions/{session_id} (detail)
# ---------------------------------------------------------------------------


def test_session_detail_returns_entries_for_one_session(client):
    _login(client)
    _append(session_id="s1", agent_id="agent:a")
    _append(session_id="s2", agent_id="agent:b")
    _append(session_id="s1", agent_id="agent:c")

    body = client.get("/api/sessions/s1").json()
    assert body["session_id"] == "s1"
    entries = body["entries"]
    assert len(entries) == 2
    assert {e["agent_id"] for e in entries} == {"agent:a", "agent:c"}


def test_session_detail_redacts_for_non_auditors(client):
    _login(client)  # admin, not auditor
    _append(
        session_id="s1",
        why_messages=[{"role": "user", "content": "secret prompt"}],
    )

    body = client.get("/api/sessions/s1").json()
    assert body["content_redacted"] is True
    # `why_last` is where the UI surfaces the last prompt snippet — must be
    # empty for non-auditors even though the field is present.
    assert body["entries"][0].get("why_last", "") == ""


def test_session_detail_unknown_id_returns_empty_entries(client):
    _login(client)
    body = client.get("/api/sessions/nonexistent").json()
    assert body["entries"] == []
