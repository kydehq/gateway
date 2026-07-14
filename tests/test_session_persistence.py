"""Tests for the DB-backed dashboard session store.

Sessions live in `auth_sessions` (migration 0018) so that a browser
cookie survives a kyde-api process restart. These tests pin the
contract the dashboard middleware depends on: get-after-mint resolves,
delete invalidates, per-user wipes leave other users alone, and an
expired row reads as not-present.
"""

from __future__ import annotations

from kyde import auth, ledger


def _seed_user(username: str, roles: list[str]) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password("CorrectHorse!Battery9"),
        roles=roles,
        must_change_password=False,
    )


# ---------------------------------------------------------------------------
# mint → get round-trip
# ---------------------------------------------------------------------------


def test_create_then_get_returns_full_context():
    user = _seed_user("alice", ["admin"])
    ledger.create_session(
        token="tok-alice",
        user_id=user["id"],
        username=user["username"],
        roles=["admin"],
        must_change_password=False,
    )

    ctx = ledger.get_session("tok-alice")
    assert ctx is not None
    assert ctx["user_id"] == user["id"]
    assert ctx["username"] == "alice"
    assert ctx["roles"] == ["admin"]
    assert ctx["must_change_password"] is False


def test_get_unknown_token_returns_none():
    assert ledger.get_session("never-existed") is None
    assert ledger.get_session("") is None


# ---------------------------------------------------------------------------
# Logout (delete_session)
# ---------------------------------------------------------------------------


def test_delete_session_invalidates_the_token():
    user = _seed_user("bob", ["viewer"])
    ledger.create_session(
        token="tok-bob",
        user_id=user["id"],
        username=user["username"],
        roles=["viewer"],
        must_change_password=False,
    )
    assert ledger.get_session("tok-bob") is not None

    ledger.delete_session("tok-bob")
    assert ledger.get_session("tok-bob") is None


# ---------------------------------------------------------------------------
# Per-user wipes (password reset, role rotation, soft delete)
# ---------------------------------------------------------------------------


def test_delete_sessions_for_user_clears_only_target():
    alice = _seed_user("alice", ["admin"])
    bob = _seed_user("bob", ["viewer"])

    for tok in ("alice-1", "alice-2"):
        ledger.create_session(
            token=tok,
            user_id=alice["id"],
            username=alice["username"],
            roles=["admin"],
            must_change_password=False,
        )
    ledger.create_session(
        token="bob-1",
        user_id=bob["id"],
        username=bob["username"],
        roles=["viewer"],
        must_change_password=False,
    )

    n = ledger.delete_sessions_for_user(alice["id"])
    assert n == 2
    assert ledger.get_session("alice-1") is None
    assert ledger.get_session("alice-2") is None
    assert ledger.get_session("bob-1") is not None


def test_delete_sessions_for_user_with_except_token_keeps_caller_alive():
    """Role-rotation flow: the admin rotating their own roles must
    keep their current session, but other sessions of the same user
    should die so the new capabilities apply immediately elsewhere."""
    user = _seed_user("alice", ["admin"])
    for tok in ("active", "stale-1", "stale-2"):
        ledger.create_session(
            token=tok,
            user_id=user["id"],
            username=user["username"],
            roles=["admin"],
            must_change_password=False,
        )

    n = ledger.delete_sessions_for_user(user["id"], except_token="active")
    assert n == 2
    assert ledger.get_session("active") is not None
    assert ledger.get_session("stale-1") is None
    assert ledger.get_session("stale-2") is None


# ---------------------------------------------------------------------------
# Expiry — past expires_at reads as not-present
# ---------------------------------------------------------------------------


def test_expired_row_does_not_authenticate():
    user = _seed_user("alice", ["admin"])
    ledger.create_session(
        token="tok-stale",
        user_id=user["id"],
        username=user["username"],
        roles=["admin"],
        must_change_password=False,
    )
    # Backdate the row past its expiry.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_sessions SET expires_at = now() - interval '1 second' "
                "WHERE token = %s",
                ("tok-stale",),
            )
        conn.commit()

    assert ledger.get_session("tok-stale") is None
    # And list_sessions_for_user excludes it too.
    assert ledger.list_sessions_for_user(user["id"]) == []


# ---------------------------------------------------------------------------
# update_session_context — _refresh_session's patch path
# ---------------------------------------------------------------------------


def test_update_session_context_patches_denormalised_fields():
    user = _seed_user("alice", ["viewer"])
    ledger.create_session(
        token="tok-alice",
        user_id=user["id"],
        username="alice",
        roles=["viewer"],
        must_change_password=True,
    )

    ledger.update_session_context(
        "tok-alice",
        username="alice",
        roles=["admin", "auditor"],
        must_change_password=False,
    )

    ctx = ledger.get_session("tok-alice")
    assert ctx is not None
    assert sorted(ctx["roles"]) == ["admin", "auditor"]
    assert ctx["must_change_password"] is False


# ---------------------------------------------------------------------------
# FK cascade — deleting the user wipes their sessions
# ---------------------------------------------------------------------------


def test_user_hard_delete_cascades_to_sessions():
    """Soft delete keeps the user row, but if a row is ever hard-deleted
    (e.g. via direct SQL maintenance) the FK cascade should clean up
    orphaned sessions automatically."""
    user = _seed_user("doomed", ["viewer"])
    ledger.create_session(
        token="tok-doomed",
        user_id=user["id"],
        username=user["username"],
        roles=["viewer"],
        must_change_password=False,
    )
    assert ledger.get_session("tok-doomed") is not None

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user["id"],))
        conn.commit()

    assert ledger.get_session("tok-doomed") is None
