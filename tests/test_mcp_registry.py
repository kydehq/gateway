"""
Tests for kyde.mcp_registry — the cached resolver over ledger CRUD.

These exercise the routing-table surface that mcp_proxy depends on:
input validation, upsert/get/list/delete semantics, and the 5-second
cache (writes must invalidate immediately so dashboard edits feel live).
"""

from __future__ import annotations

import pytest

from kyde import ledger, mcp_registry

# ---------------------------------------------------------------------------
# Validation — happens before any DB write so operator typos surface early.
# ---------------------------------------------------------------------------


def test_upsert_rejects_invalid_name_characters():
    with pytest.raises(ValueError, match="server name"):
        mcp_registry.upsert_server("Bad Name", "https://example.test/mcp")


def test_upsert_rejects_name_starting_with_hyphen():
    with pytest.raises(ValueError, match="server name"):
        mcp_registry.upsert_server("-leading", "https://example.test/mcp")


def test_upsert_rejects_name_too_long():
    with pytest.raises(ValueError, match="server name"):
        mcp_registry.upsert_server("a" * 64, "https://example.test/mcp")


def test_upsert_accepts_max_length_and_punctuation():
    name = "a" + "b" * 62  # exactly 63 chars
    row = mcp_registry.upsert_server(name, "https://example.test/mcp")
    assert row["name"] == name

    row2 = mcp_registry.upsert_server("gh-1_test", "https://example.test/mcp")
    assert row2["name"] == "gh-1_test"


def test_upsert_rejects_non_http_url():
    with pytest.raises(ValueError, match="upstream_url"):
        mcp_registry.upsert_server("ok", "ftp://example.test/mcp")


def test_upsert_rejects_truncated_url():
    with pytest.raises(ValueError, match="upstream_url"):
        mcp_registry.upsert_server("ok", "https:/")


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_upsert_then_get_returns_persisted_row():
    mcp_registry.upsert_server("github", "https://api.githubcopilot.com/mcp/")
    fetched = mcp_registry.get_server("github")
    assert fetched is not None
    assert fetched["upstream_url"] == "https://api.githubcopilot.com/mcp/"
    assert fetched["enabled"] is True
    assert fetched["tenant_id"] == mcp_registry.DEFAULT_TENANT


def test_get_returns_none_for_unknown_server():
    assert mcp_registry.get_server("does-not-exist") is None


def test_upsert_updates_existing_row_in_place():
    first = mcp_registry.upsert_server("svc", "https://a.test/mcp")
    second = mcp_registry.upsert_server("svc", "https://b.test/mcp", enabled=False)
    # Same id — this is an update, not a duplicate insert.
    assert first["id"] == second["id"]
    assert second["upstream_url"] == "https://b.test/mcp"
    assert second["enabled"] is False


def test_list_servers_returns_all_for_tenant_sorted():
    mcp_registry.upsert_server("zeta", "https://z.test/mcp")
    mcp_registry.upsert_server("alpha", "https://a.test/mcp")
    rows = mcp_registry.list_servers()
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    assert {"zeta", "alpha"}.issubset(set(names))


def test_delete_removes_row_and_returns_true():
    mcp_registry.upsert_server("temp", "https://t.test/mcp")
    assert mcp_registry.delete_server("temp") is True
    assert mcp_registry.get_server("temp") is None


def test_delete_returns_false_when_nothing_to_delete():
    assert mcp_registry.delete_server("never-existed") is False


# ---------------------------------------------------------------------------
# Cache — invariant: writes invalidate so the dashboard feels live.
# ---------------------------------------------------------------------------


def test_writes_invalidate_single_server_cache():
    mcp_registry.upsert_server("svc", "https://a.test/mcp")
    assert mcp_registry.get_server("svc")["upstream_url"] == "https://a.test/mcp"

    # Mutate via the ledger directly, *without* going through the registry.
    # If the cache weren't invalidated by upsert_server below, the stale
    # 'a.test' value would still surface for up to 5 s.
    mcp_registry.upsert_server("svc", "https://b.test/mcp")
    assert mcp_registry.get_server("svc")["upstream_url"] == "https://b.test/mcp"


def test_writes_invalidate_list_cache():
    mcp_registry.upsert_server("one", "https://1.test/mcp")
    assert [r["name"] for r in mcp_registry.list_servers()] == ["one"]
    mcp_registry.upsert_server("two", "https://2.test/mcp")
    names = {r["name"] for r in mcp_registry.list_servers()}
    assert names == {"one", "two"}


def test_delete_invalidates_cache():
    mcp_registry.upsert_server("svc", "https://a.test/mcp")
    assert mcp_registry.get_server("svc") is not None
    mcp_registry.delete_server("svc")
    assert mcp_registry.get_server("svc") is None


def test_cache_serves_repeated_lookups_without_hitting_ledger(monkeypatch):
    """Two reads in a row should hit the ledger at most once."""
    mcp_registry.upsert_server("svc", "https://a.test/mcp")
    mcp_registry.invalidate_cache()  # start cold

    calls = {"n": 0}
    real = ledger.get_mcp_server

    def counting(tenant_id, name):
        calls["n"] += 1
        return real(tenant_id, name)

    monkeypatch.setattr(ledger, "get_mcp_server", counting)
    mcp_registry.get_server("svc")
    mcp_registry.get_server("svc")
    mcp_registry.get_server("svc")
    assert calls["n"] == 1


def test_invalidate_cache_scoped_to_tenant():
    """Invalidating tenant A must leave tenant B's cache intact."""
    mcp_registry.upsert_server("svc", "https://a.test/mcp", tenant_id="tenant-a")
    mcp_registry.upsert_server("svc", "https://b.test/mcp", tenant_id="tenant-b")
    # Warm both caches.
    mcp_registry.get_server("svc", tenant_id="tenant-a")
    mcp_registry.get_server("svc", tenant_id="tenant-b")

    mcp_registry.invalidate_cache(tenant_id="tenant-a")
    # tenant-b key should still be present in the cache dict.
    assert (
        "tenant-b",
        "svc",
    ) in mcp_registry._cache_servers, "tenant-b cache entry was incorrectly evicted"
    assert (
        "tenant-a",
        "svc",
    ) not in mcp_registry._cache_servers, "tenant-a cache entry was not evicted"


def teardown_function(_func):
    """Per-test cache reset — DB truncation alone wouldn't clear the in-process cache."""
    mcp_registry.invalidate_cache()
