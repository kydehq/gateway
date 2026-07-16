"""
Tests for the hostname resolver + cache (Phase 2).

DNS is mocked at the `_reverse_dns_sync` seam so CI never touches the
real system resolver.
"""

import asyncio
from unittest.mock import patch

import pytest

from kyde import host_resolver, ledger

# ---------------------------------------------------------------------------
# reverse_dns (the async wrapper)
# ---------------------------------------------------------------------------


def test_reverse_dns_returns_none_for_empty_ip():
    result = asyncio.run(host_resolver.reverse_dns(""))
    assert result is None


def test_reverse_dns_propagates_resolver_result():
    with patch.object(
        host_resolver, "_reverse_dns_sync", return_value="api.example.com"
    ):
        result = asyncio.run(host_resolver.reverse_dns("203.0.113.5"))
    assert result == "api.example.com"


def test_reverse_dns_returns_none_on_resolver_failure():
    with patch.object(host_resolver, "_reverse_dns_sync", return_value=None):
        result = asyncio.run(host_resolver.reverse_dns("203.0.113.5"))
    assert result is None


def test_reverse_dns_returns_none_on_timeout():
    import time as _time

    def slow(_ip: str):
        _time.sleep(2.0)  # Way past the 0.05s timeout we use here.
        return "should-not-be-returned"

    with patch.object(host_resolver, "_reverse_dns_sync", side_effect=slow):
        result = asyncio.run(host_resolver.reverse_dns("203.0.113.5", timeout=0.05))
    assert result is None


# ---------------------------------------------------------------------------
# resolve_and_cache — the public entry point used by /api/topology/ip
# ---------------------------------------------------------------------------


def test_resolve_and_cache_populates_dns_row_on_miss():
    with patch.object(host_resolver, "_reverse_dns_sync", return_value="crm.internal"):
        result = asyncio.run(host_resolver.resolve_and_cache("10.4.0.1"))
    assert result.ip == "10.4.0.1"
    assert result.hostname == "crm.internal"
    assert result.source == "dns"
    # Persisted.
    cached = ledger.get_host_resolution("10.4.0.1")
    assert cached is not None
    assert cached["hostname"] == "crm.internal"
    assert cached["source"] == "dns"


def test_resolve_and_cache_caches_misses_too():
    with patch.object(host_resolver, "_reverse_dns_sync", return_value=None):
        result = asyncio.run(host_resolver.resolve_and_cache("203.0.113.99"))
    assert result.hostname is None
    assert result.source == "dns"
    cached = ledger.get_host_resolution("203.0.113.99")
    assert cached is not None
    assert cached["hostname"] is None  # cached miss


def test_resolve_and_cache_returns_cached_dns_without_calling_resolver():
    # Seed a fresh DNS row.
    ledger.upsert_host_dns(ip="10.4.0.2", hostname="api.internal", ttl_seconds=86400)
    # If the resolver is called, the test will visibly diverge.
    with patch.object(
        host_resolver,
        "_reverse_dns_sync",
        side_effect=AssertionError("should not be called"),
    ):
        result = asyncio.run(host_resolver.resolve_and_cache("10.4.0.2"))
    assert result.hostname == "api.internal"


def test_resolve_and_cache_admin_label_always_wins():
    ledger.upsert_host_label(ip="10.4.0.3", hostname="canonical-name.corp")
    # Even with a real DNS answer ready, admin wins.
    with patch.object(
        host_resolver, "_reverse_dns_sync", return_value="different.example.com"
    ):
        result = asyncio.run(host_resolver.resolve_and_cache("10.4.0.3"))
    assert result.hostname == "canonical-name.corp"
    assert result.source == "admin"


def test_resolve_and_cache_force_refreshes_stale_dns():
    # Seed an expired DNS row directly via SQL so we don't have to wait
    # for a real TTL to elapse.
    import time

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO host_resolutions (ip, hostname, source, resolved_at, ttl_seconds) "
                "VALUES (%s, %s, 'dns', to_timestamp(%s), %s)",
                ("10.4.0.4", "old-name.local", time.time() - 7200, 60),
            )
        conn.commit()

    with patch.object(
        host_resolver, "_reverse_dns_sync", return_value="new-name.local"
    ):
        result = asyncio.run(host_resolver.resolve_and_cache("10.4.0.4"))
    assert result.hostname == "new-name.local"


def test_resolve_and_cache_force_does_not_overwrite_admin():
    ledger.upsert_host_label(ip="10.4.0.5", hostname="frozen.corp")
    with patch.object(
        host_resolver, "_reverse_dns_sync", return_value="dns-name.local"
    ):
        result = asyncio.run(host_resolver.resolve_and_cache("10.4.0.5", force=True))
    assert result.hostname == "frozen.corp"
    assert result.source == "admin"


# ---------------------------------------------------------------------------
# Admin precedence at the ledger layer — guarded explicitly so a future
# code change in host_resolver can't accidentally lose this invariant.
# ---------------------------------------------------------------------------


def test_upsert_host_dns_is_noop_when_admin_row_exists():
    ledger.upsert_host_label(ip="10.4.0.6", hostname="admin-name")
    ledger.upsert_host_dns(ip="10.4.0.6", hostname="dns-name", ttl_seconds=3600)
    row = ledger.get_host_resolution("10.4.0.6")
    assert row["hostname"] == "admin-name"
    assert row["source"] == "admin"


def test_delete_host_label_removes_admin_row():
    ledger.upsert_host_label(ip="10.4.0.7", hostname="admin-name")
    assert ledger.delete_host_label("10.4.0.7") is True
    assert ledger.get_host_resolution("10.4.0.7") is None


def test_upsert_host_label_rejects_empty_hostname():
    with pytest.raises(ValueError):
        ledger.upsert_host_label(ip="10.4.0.8", hostname="   ")


# ---------------------------------------------------------------------------
# Reverse lookup — multiple IPs per hostname
# ---------------------------------------------------------------------------


def test_find_ips_for_hostname_returns_multiple_ips():
    ledger.upsert_host_label(ip="10.4.0.10", hostname="round-robin.local")
    ledger.upsert_host_label(ip="10.4.0.11", hostname="round-robin.local")

    ips = ledger.find_ips_for_hostname("round-robin.local")
    assert {row["ip"] for row in ips} == {"10.4.0.10", "10.4.0.11"}


def test_find_most_recent_ip_picks_most_recent_traffic():
    """When two IPs share a hostname, the lookup picks the one that's
    been seen most recently in request_network."""
    import time

    ledger.upsert_host_label(ip="10.4.0.20", hostname="lb.internal")
    ledger.upsert_host_label(ip="10.4.0.21", hostname="lb.internal")

    now = time.time()
    # request_network FKs ledger.seq — seed ledger first.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger (seq, entry_id, timestamp, agent_id,"
                " action_type, model, input_hash, output_hash, prev_hash,"
                " entry_hash, signature)"
                " VALUES (1, 'a-old', %s, 'x', 'chat', 'm',"
                "         '0','0','0','0','s'),"
                "        (2, 'b-new', %s, 'y', 'chat', 'm',"
                "         '0','0','0','0','s')",
                (now - 3600, now - 60),
            )
            cur.execute(
                "INSERT INTO request_network (seq, timestamp, remote_addr) "
                "VALUES (1, %s, %s::inet), (2, %s, %s::inet)",
                (now - 3600, "10.4.0.20", now - 60, "10.4.0.21"),
            )
        conn.commit()

    ip = ledger.find_most_recent_ip_for_hostname("lb.internal")
    assert ip == "10.4.0.21"


# ---------------------------------------------------------------------------
# Host Names table for Settings UI
# ---------------------------------------------------------------------------


def _seed_observed_ip(seq: int, timestamp: float, ip: str):
    """Helper: insert a ledger row + matching request_network row."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger (seq, entry_id, timestamp, agent_id,"
                " action_type, model, input_hash, output_hash, prev_hash,"
                " entry_hash, signature)"
                " VALUES (%s, %s, %s, 'x', 'chat', 'm', '0','0','0','0','s')",
                (seq, f"e-{seq}", timestamp),
            )
            cur.execute(
                "INSERT INTO request_network (seq, timestamp, remote_addr) "
                "VALUES (%s, %s, %s::inet)",
                (seq, timestamp, ip),
            )
        conn.commit()


def test_list_host_resolutions_status_labeled():
    _seed_observed_ip(1, 1700000000.0, "10.4.0.30")
    ledger.upsert_host_label(ip="10.4.0.30", hostname="labeled.local")

    rows = ledger.list_host_resolutions(status="labeled")
    assert len(rows) == 1
    assert rows[0]["ip"] == "10.4.0.30"
    assert rows[0]["hostname"] == "labeled.local"
    assert rows[0]["source"] == "admin"


def test_list_host_resolutions_status_unlabeled():
    _seed_observed_ip(2, 1700000000.0, "10.4.0.31")
    _seed_observed_ip(3, 1700000001.0, "10.4.0.32")
    ledger.upsert_host_label(ip="10.4.0.32", hostname="labeled.local")

    rows = ledger.list_host_resolutions(status="unlabeled")
    ips = {r["ip"] for r in rows}
    assert "10.4.0.31" in ips  # observed, no label
    assert "10.4.0.32" not in ips  # has admin label


def test_list_host_resolutions_search_matches_ip_or_hostname():
    _seed_observed_ip(4, 1700000000.0, "10.4.0.40")
    ledger.upsert_host_label(ip="10.4.0.40", hostname="search-target.local")

    by_ip = ledger.list_host_resolutions(status="all", search="10.4.0.40")
    by_host = ledger.list_host_resolutions(status="all", search="search-target")
    assert any(r["ip"] == "10.4.0.40" for r in by_ip)
    assert any(r["hostname"] == "search-target.local" for r in by_host)


def test_get_cached_returns_none_for_stale_dns():
    """get_cached is the zero-call decoration path — it must not return
    stale dns rows. Otherwise the dashboard could show 7-day-old
    hostnames as if they were fresh."""
    import time

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO host_resolutions (ip, hostname, source, resolved_at, ttl_seconds) "
                "VALUES (%s, %s, 'dns', to_timestamp(%s), %s)",
                ("10.4.0.50", "old.local", time.time() - 999999, 60),
            )
        conn.commit()
    assert host_resolver.get_cached("10.4.0.50") is None


def test_get_cached_returns_admin_row_unconditionally():
    """Admin rows have no TTL — get_cached must return them even if
    resolved_at is ancient."""
    import time

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO host_resolutions (ip, hostname, source, resolved_at, ttl_seconds) "
                "VALUES (%s, %s, 'admin', to_timestamp(%s), %s)",
                ("10.4.0.51", "ancient.corp", time.time() - 999999, 0),
            )
        conn.commit()
    cached = host_resolver.get_cached("10.4.0.51")
    assert cached is not None
    assert cached.hostname == "ancient.corp"
    assert cached.source == "admin"
