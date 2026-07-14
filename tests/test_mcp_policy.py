"""Tests for kyde.mcp_policy — the most-specific-wins policy resolver
that the MCP proxy consults before forwarding a tools/call.

The contract under test:
  * Default-allow when no row matches.
  * Precedence: (server, agent, tool) > (server, *, tool) >
                (server, agent, *)  > (server, *, *).
  * Writes and deletes invalidate the in-process cache immediately so a
    dashboard edit takes effect on the next proxy call without waiting
    for the 5-second TTL to expire.
"""

from __future__ import annotations

from kyde import ledger, mcp_policy, mcp_registry


def _register() -> str:
    row = mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    return str(row["id"])


def teardown_function(_):
    mcp_policy.invalidate_cache()
    mcp_registry.invalidate_cache()


# ---------------------------------------------------------------------------
# Default behaviour
# ---------------------------------------------------------------------------


def test_no_rows_defaults_to_allow():
    server_id = _register()
    decision, reason = mcp_policy.check_policy(server_id, "agent:x", "search")
    assert decision == "allow"
    assert reason is None


# ---------------------------------------------------------------------------
# Precedence ladder — each rung wins over everything below it.
# ---------------------------------------------------------------------------


def test_exact_match_wins_over_wildcard_agent():
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "*", "search", "deny", "global", None)
    mcp_policy.upsert_policy(server_id, "agent:x", "search", "allow", "per-agent", None)
    decision, reason = mcp_policy.check_policy(server_id, "agent:x", "search")
    assert decision == "allow"
    assert reason == "per-agent"


def test_wildcard_agent_specific_tool_wins_over_specific_agent_wildcard_tool():
    """When both a tool-specific global rule and an agent-wide rule exist,
    the more specific tool name wins — matches the ladder in the docstring."""
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "*", "search", "deny", "tool-global", None)
    mcp_policy.upsert_policy(server_id, "agent:x", "*", "allow", "agent-wide", None)
    decision, reason = mcp_policy.check_policy(server_id, "agent:x", "search")
    assert decision == "deny"
    assert reason == "tool-global"


def test_specific_agent_wildcard_tool_wins_over_full_wildcard():
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "*", "*", "deny", "fallback", None)
    mcp_policy.upsert_policy(server_id, "agent:x", "*", "allow", "agent-wide", None)
    decision, reason = mcp_policy.check_policy(server_id, "agent:x", "search")
    assert decision == "allow"
    assert reason == "agent-wide"


def test_full_wildcard_applies_when_nothing_more_specific_matches():
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "*", "*", "deny", "fallback", None)
    decision, reason = mcp_policy.check_policy(server_id, "agent:x", "search")
    assert decision == "deny"
    assert reason == "fallback"


def test_other_agents_unaffected_by_per_agent_rule():
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "agent:x", "search", "deny", "specific", None)
    decision, _ = mcp_policy.check_policy(server_id, "agent:y", "search")
    assert decision == "allow"


# ---------------------------------------------------------------------------
# Cache invariants — writes invalidate, repeated reads served from cache.
# ---------------------------------------------------------------------------


def test_upsert_invalidates_cache_in_same_process():
    server_id = _register()
    assert mcp_policy.check_policy(server_id, "a", "t") == ("allow", None)
    mcp_policy.upsert_policy(server_id, "a", "t", "deny", "now", None)
    assert mcp_policy.check_policy(server_id, "a", "t") == ("deny", "now")


def test_delete_invalidates_cache():
    server_id = _register()
    mcp_policy.upsert_policy(server_id, "a", "t", "deny", "tmp", None)
    assert mcp_policy.check_policy(server_id, "a", "t") == ("deny", "tmp")
    mcp_policy.delete_policy(server_id, "a", "t")
    assert mcp_policy.check_policy(server_id, "a", "t") == ("allow", None)


def test_repeated_reads_served_from_cache(monkeypatch):
    """Three consecutive checks should hit the ledger at most once."""
    server_id = _register()
    mcp_policy.invalidate_cache()

    calls = {"n": 0}
    real = ledger.list_mcp_tool_policies

    def counting(sid):
        calls["n"] += 1
        return real(sid)

    monkeypatch.setattr(ledger, "list_mcp_tool_policies", counting)
    mcp_policy.check_policy(server_id, "a", "t")
    mcp_policy.check_policy(server_id, "a", "t")
    mcp_policy.check_policy(server_id, "b", "t2")
    assert calls["n"] == 1


def test_invalidate_cache_scoped_to_one_server():
    s1 = _register()
    s2 = str(mcp_registry.upsert_server("svc2", "https://other.test/mcp")["id"])
    # Warm both.
    mcp_policy.check_policy(s1, "a", "t")
    mcp_policy.check_policy(s2, "a", "t")
    assert s1 in mcp_policy._cache and s2 in mcp_policy._cache

    mcp_policy.invalidate_cache(s1)
    assert s1 not in mcp_policy._cache
    assert s2 in mcp_policy._cache


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_upsert_rejects_unknown_decision():
    server_id = _register()
    try:
        mcp_policy.upsert_policy(server_id, "a", "t", "maybe", None, None)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "decision" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown decision")
