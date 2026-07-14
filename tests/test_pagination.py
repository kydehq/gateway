"""
Tests for the paginated ledger helpers that back the dashboard's Timeline
and Sessions panels. The underlying constraints are:

- `list_entries_paginated` must honor `seq`-based cursors (stable under
  concurrent inserts), filter on action/upstream/agent_id as equality, and
  run substring search through the pg_trgm GIN index.
- `entry_facets` powers the filter dropdowns — one cheap call, returns
  distinct action_types and upstreams currently in the ledger.
- `list_session_summaries` aggregates by session_id with a time-ordered
  cursor, one query (no Python post-processing).
- `get_session_detail` returns the entries for one session in a single
  indexed lookup.
"""

from __future__ import annotations

from typing import Any

from kyde import ledger


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
# list_entries_paginated
# ---------------------------------------------------------------------------


def test_paginated_returns_newest_first_with_cursor():
    _entries = [_append(agent_id=f"agent:{i}") for i in range(7)]
    # Newest first: seq 7, 6, 5 ... (entries[-1] is seq 7)
    page1 = ledger.list_entries_paginated(limit=3)
    assert [e["seq"] for e in page1["items"]] == [7, 6, 5]
    assert page1["has_more"] is True
    assert page1["next_cursor"] == 5

    page2 = ledger.list_entries_paginated(limit=3, cursor=page1["next_cursor"])
    assert [e["seq"] for e in page2["items"]] == [4, 3, 2]
    assert page2["has_more"] is True

    page3 = ledger.list_entries_paginated(limit=3, cursor=page2["next_cursor"])
    assert [e["seq"] for e in page3["items"]] == [1]
    assert page3["has_more"] is False
    assert page3["next_cursor"] is None


def test_paginated_empty():
    r = ledger.list_entries_paginated(limit=10)
    assert r["items"] == []
    assert r["next_cursor"] is None
    assert r["has_more"] is False


def test_paginated_filter_by_action():
    _append(action_type="chat")
    _append(action_type="tool_call")
    _append(action_type="chat")

    r = ledger.list_entries_paginated(limit=10, action="chat")
    assert len(r["items"]) == 2
    assert all(e["action_type"] == "chat" for e in r["items"])


def test_paginated_filter_by_upstream():
    _append(upstream="openai")
    _append(upstream="anthropic")
    _append(upstream="openai")

    r = ledger.list_entries_paginated(limit=10, upstream="openai")
    assert len(r["items"]) == 2


def test_paginated_filter_by_agent_id():
    _append(agent_id="agent:alpha")
    _append(agent_id="agent:bravo")
    _append(agent_id="agent:alpha")

    r = ledger.list_entries_paginated(limit=10, agent_id="agent:alpha")
    assert len(r["items"]) == 2


def test_paginated_search_matches_agent_id_substring():
    _append(agent_id="agent:production-worker")
    _append(agent_id="agent:staging-worker")
    _append(agent_id="agent:dev-worker")

    r = ledger.list_entries_paginated(limit=10, search="produc")
    assert len(r["items"]) == 1
    assert r["items"][0]["agent_id"] == "agent:production-worker"


def test_paginated_search_matches_model():
    _append(model="gpt-4o")
    _append(model="claude-3-opus")
    _append(model="gpt-3.5-turbo")

    r = ledger.list_entries_paginated(limit=10, search="gpt")
    assert len(r["items"]) == 2


def test_paginated_search_matches_session_id():
    _append(session_id="sess-payments-2026")
    _append(session_id="sess-auth-2026")

    r = ledger.list_entries_paginated(limit=10, search="paymen")
    assert len(r["items"]) == 1
    assert r["items"][0]["session_id"] == "sess-payments-2026"


def test_paginated_search_is_case_insensitive():
    _append(agent_id="AGENT:LOUD")
    r = ledger.list_entries_paginated(limit=10, search="loud")
    assert len(r["items"]) == 1


def test_paginated_combines_filters_and_search():
    _append(action_type="chat", agent_id="agent:alpha", upstream="openai")
    _append(action_type="tool_call", agent_id="agent:alpha", upstream="openai")
    _append(action_type="chat", agent_id="agent:bravo", upstream="openai")

    r = ledger.list_entries_paginated(
        limit=10, action="chat", upstream="openai", search="alpha"
    )
    assert len(r["items"]) == 1
    assert r["items"][0]["agent_id"] == "agent:alpha"
    assert r["items"][0]["action_type"] == "chat"


def test_paginated_cursor_is_stable_across_concurrent_inserts():
    for i in range(5):
        _append(agent_id=f"agent:old-{i}")
    page1 = ledger.list_entries_paginated(limit=3)
    # Simulate concurrent inserts between pages — offset pagination would
    # skip/duplicate rows here; cursor pagination shouldn't.
    for i in range(10):
        _append(agent_id=f"agent:new-{i}")
    page2 = ledger.list_entries_paginated(limit=3, cursor=page1["next_cursor"])
    seqs = [e["seq"] for e in page2["items"]]
    assert seqs == [2, 1]  # the remaining originals, nothing from the new batch
    assert page2["has_more"] is False


def test_paginated_jsonb_columns_come_back_parsed():
    _append(tool_calls=[{"function": "ls", "args": {}}])
    r = ledger.list_entries_paginated(limit=1)
    tc = r["items"][0]["tool_calls"]
    assert isinstance(tc, list)
    assert tc[0]["function"] == "ls"


# ---------------------------------------------------------------------------
# entry_facets
# ---------------------------------------------------------------------------


def test_entry_facets_empty():
    f = ledger.entry_facets()
    assert f == {"actions": [], "upstreams": []}


def test_entry_facets_returns_distinct_values_sorted():
    _append(action_type="chat", upstream="openai")
    _append(action_type="chat", upstream="anthropic")
    _append(action_type="tool_call", upstream="anthropic")

    f = ledger.entry_facets()
    assert f["actions"] == ["chat", "tool_call"]
    assert f["upstreams"] == ["anthropic", "openai"]


def test_entry_facets_excludes_empty_upstream():
    _append(upstream="openai")
    _append(upstream="")  # synthetic-no-upstream rows shouldn't pollute the dropdown
    f = ledger.entry_facets()
    assert "" not in f["upstreams"]
    assert f["upstreams"] == ["openai"]


# ---------------------------------------------------------------------------
# Session summary + detail
# ---------------------------------------------------------------------------


def test_session_summaries_aggregate_by_session_id():
    _append(session_id="s1", agent_id="agent:a")
    _append(session_id="s1", agent_id="agent:b")
    _append(session_id="s2", agent_id="agent:a")

    r = ledger.list_session_summaries(limit=10)
    ids = {s["session_id"] for s in r["items"]}
    assert ids == {"s1", "s2"}
    s1 = next(s for s in r["items"] if s["session_id"] == "s1")
    assert s1["entry_count"] == 2
    assert s1["agent_count"] == 2
    assert set(s1["agents"]) == {"agent:a", "agent:b"}


def test_session_summaries_sorted_by_last_activity_desc():
    _append(session_id="oldest")
    _append(session_id="middle")
    _append(session_id="newest")

    r = ledger.list_session_summaries(limit=10)
    assert [s["session_id"] for s in r["items"]] == ["newest", "middle", "oldest"]


def test_session_summaries_skip_empty_session_id():
    _append(session_id="")
    _append(session_id="real")
    r = ledger.list_session_summaries(limit=10)
    assert [s["session_id"] for s in r["items"]] == ["real"]


def test_session_summaries_paginate_by_last_time_cursor():
    # Three sessions, each with one entry — ordered newest → oldest by append.
    _append(session_id="s1")
    _append(session_id="s2")
    _append(session_id="s3")

    page1 = ledger.list_session_summaries(limit=2)
    assert [s["session_id"] for s in page1["items"]] == ["s3", "s2"]
    assert page1["has_more"] is True

    page2 = ledger.list_session_summaries(limit=2, cursor=page1["next_cursor"])
    assert [s["session_id"] for s in page2["items"]] == ["s1"]
    assert page2["has_more"] is False


def test_get_session_detail_returns_entries_for_one_session():
    _append(session_id="s1", agent_id="a")
    _append(session_id="s2", agent_id="b")
    _append(session_id="s1", agent_id="c")

    rows = ledger.get_session_detail("s1")
    assert len(rows) == 2
    assert {r["agent_id"] for r in rows} == {"a", "c"}
    assert all(r["session_id"] == "s1" for r in rows)


def test_get_session_detail_unknown_session_is_empty():
    assert ledger.get_session_detail("nonexistent") == []
