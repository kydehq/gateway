"""
Direct tests of kyde.ledger — no HTTP, no FastAPI. These exercise:

- hash-chain integrity across serial appends
- concurrent appends under the pg_advisory_xact_lock serializer
- JSONB-backed user queries (any_admin_exists, count_active_admins)
- soft delete behavior
- verify_chain tamper detection
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from kyde import auth, ledger

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _append_simple(
    agent_id: str = "agent:test", **overrides: Any
) -> ledger.LedgerEntry:
    defaults = dict(
        agent_id=agent_id,
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )
    defaults.update(overrides)
    return ledger.append(**defaults)


def _mk_user(
    username: str, roles: list[str], password: str = "CorrectHorse!Battery9"
) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(password),
        roles=roles,
        must_change_password=False,
    )


# ---------------------------------------------------------------------------
# Append + chain
# ---------------------------------------------------------------------------


def test_append_links_to_previous_entry():
    first = _append_simple("agent:a")
    second = _append_simple("agent:b")

    assert first.prev_hash == ledger.GENESIS_HASH
    assert second.prev_hash == first.entry_hash
    # entry_hash must be stable across reads — re-fetch and compare.
    fetched = ledger.get_entry(second.entry_id)
    assert fetched is not None
    assert fetched["entry_hash"] == second.entry_hash


def test_append_stores_response_body_verbatim():
    body = {"choices": [{"message": {"content": "hello"}}], "model": "gpt-4o-mini"}
    e = _append_simple(response_body=body)

    fetched = ledger.get_entry(e.entry_id)
    assert fetched is not None
    assert fetched["response_body"] == body
    # The stored body must hash back to the signed output_hash — that's the
    # whole point of storing the exact dict append() hashed (migration 0022).
    assert ledger._hash_dict(fetched["response_body"]) == fetched["output_hash"]


def test_verify_chain_empty():
    valid, errors = ledger.verify_chain()
    assert valid is True
    assert errors == []


def test_verify_chain_intact_after_appends():
    for i in range(5):
        _append_simple(agent_id=f"agent:{i}")
    valid, errors = ledger.verify_chain()
    assert valid, f"expected clean chain, got {errors}"


# NOTE: test_verify_chain_detects_tampering moved to the kyde-enterprise repo —
# corrupting agent_id is only caught by the Ed25519 signature check, which ships
# in the enterprise edition. The starter build's unsigned hash chain trusts the stored
# entry_hash, so that tamper case has no meaning here.


def test_concurrent_appends_maintain_chain():
    """50 threads racing to append — the advisory lock must serialize them."""
    N = 50
    errors_out: list[str] = []

    def do_append(i: int) -> None:
        try:
            _append_simple(agent_id=f"agent:{i}")
        except Exception as exc:  # pragma: no cover — make failures visible
            errors_out.append(f"{i}: {exc}")

    threads = [threading.Thread(target=do_append, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors_out, errors_out
    assert ledger.count_entries() == N
    valid, chain_errors = ledger.verify_chain()
    assert valid, f"chain broke under concurrency: {chain_errors}"


# ---------------------------------------------------------------------------
# User queries — the reason we moved to JSONB
# ---------------------------------------------------------------------------


def test_any_admin_exists_with_jsonb_roles():
    assert ledger.any_admin_exists() is False
    _mk_user("alice", ["viewer"])
    assert ledger.any_admin_exists() is False
    _mk_user("bob", ["admin"])
    assert ledger.any_admin_exists() is True


def test_any_admin_exists_ignores_soft_deleted():
    admin = _mk_user("bob", ["admin"])
    assert ledger.any_admin_exists() is True
    ledger.soft_delete_user(admin["id"])
    assert ledger.any_admin_exists() is False


def test_count_active_admins_with_exclusion():
    a1 = _mk_user("a1", ["admin"])
    a2 = _mk_user("a2", ["admin"])
    _mk_user("viewer", ["viewer"])

    assert ledger.count_active_admins() == 2
    assert ledger.count_active_admins(exclude_user_id=a1["id"]) == 1
    assert ledger.count_active_admins(exclude_user_id=a2["id"]) == 1


def test_count_active_admins_skips_disabled():
    a1 = _mk_user("a1", ["admin"])
    _a2 = _mk_user("a2", ["admin"])
    assert ledger.count_active_admins() == 2
    ledger.update_user(a1["id"], enabled=False)
    assert ledger.count_active_admins() == 1


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def test_create_user_normalizes_roles():
    u = _mk_user("dup", ["viewer", "viewer", "admin"])
    assert u["roles"] == ["admin", "viewer"]  # sorted + deduped


def test_create_user_rejects_unknown_role():
    with pytest.raises(ValueError):
        ledger.create_user(
            username="x",
            email="x@x",
            password_hash="stub",
            roles=["wizard"],
            must_change_password=False,
        )


def test_create_user_requires_role():
    with pytest.raises(ValueError):
        ledger.create_user(
            username="x",
            email="x@x",
            password_hash="stub",
            roles=[],
            must_change_password=False,
        )


def test_update_user_roles_persists_as_jsonb():
    u = _mk_user("alice", ["viewer"])
    ledger.update_user(u["id"], roles=["admin", "auditor"])
    fresh = ledger.get_user_by_id(u["id"])
    assert fresh is not None
    assert fresh["roles"] == ["admin", "auditor"]


def test_soft_delete_hides_from_default_queries():
    u = _mk_user("alice", ["viewer"])
    ledger.soft_delete_user(u["id"])
    assert ledger.get_user_by_id(u["id"]) is None
    assert ledger.get_user_by_id(u["id"], include_deleted=True) is not None


def test_list_users_respects_include_deleted_flag():
    _alive = _mk_user("alive", ["viewer"])
    dead = _mk_user("dead", ["viewer"])
    ledger.soft_delete_user(dead["id"])

    active = ledger.list_users()
    assert {u["username"] for u in active} == {"alive"}

    everything = ledger.list_users(include_deleted=True)
    assert {u["username"] for u in everything} == {"alive", "dead"}


def test_lockout_after_threshold_failures():
    u = _mk_user("alice", ["viewer"])
    for _ in range(ledger.LOCKOUT_THRESHOLD):
        ledger.record_login_failure(u["id"])
    fresh = ledger.get_user_by_id(u["id"])
    assert fresh is not None
    assert fresh["locked"] is True


def test_record_login_success_clears_failures():
    u = _mk_user("alice", ["viewer"])
    ledger.record_login_failure(u["id"])
    ledger.record_login_success(u["id"])
    fresh = ledger.get_user_by_id(u["id"])
    assert fresh is not None
    assert fresh["failed_login_count"] == 0


# ---------------------------------------------------------------------------
# JSONB round-trip for ledger payload fields
# ---------------------------------------------------------------------------


def test_jsonb_columns_roundtrip_as_python_objects():
    tool_calls = [{"function": "read_file", "args": {"path": "/etc/hosts"}}]
    _full_messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "ls /"},
    ]
    e = _append_simple(tool_calls=tool_calls)

    fetched = ledger.get_entry(e.entry_id)
    assert fetched is not None
    # psycopg returns JSONB as native Python lists/dicts, not JSON strings.
    assert fetched["tool_calls"] == tool_calls
    assert isinstance(fetched["tool_calls"], list)
    assert isinstance(fetched["why"], list)


def test_get_entry_by_ref_supports_seq_uuid_and_prefix():
    e = _append_simple()
    assert ledger.get_entry_by_ref("1") is not None
    assert ledger.get_entry_by_ref(e.entry_id) is not None
    assert ledger.get_entry_by_ref(e.entry_id[:8]) is not None
    assert ledger.get_entry_by_ref("deadbeef" * 4) is None
