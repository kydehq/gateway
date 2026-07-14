"""
Tests for session_id normalization (Item 1 in the UI follow-up plan).

Verifies:
  1. Newly minted session_ids from server._session_id() are bare UUID v4
     strings — no `s-` or `session:` prefix.
  2. Migration 0002 is recorded in schema_migrations.
  3. The migration would correctly rewrite legacy session_ids if run against
     legacy data (verified by re-applying the rewrite SQL to seeded rows).
"""

import re
import time
import uuid


from kyde import ledger

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


def test_new_session_id_is_bare_uuid():
    from kyde.server import _session_id

    messages = [
        {"role": "user", "content": "Tell me about distributed databases."},
        {"role": "assistant", "content": "Sure, let me explain..."},
    ]
    sid, hashes = _session_id(_FakeRequest(), messages)
    assert _UUID_RE.match(sid), f"expected bare UUID, got {sid!r}"
    assert not sid.startswith("s-")
    assert not sid.startswith("session:")
    assert hashes, "substantive turns should produce turn hashes"


def test_new_session_id_for_short_messages_is_also_uuid():
    # The old code returned `session:<sha256>` for conversations with no
    # substantive turns. The normalized version mints a UUID instead.
    from kyde.server import _session_id

    sid, hashes = _session_id(_FakeRequest(), [{"role": "user", "content": "hi"}])
    assert _UUID_RE.match(sid), f"expected bare UUID for short msg, got {sid!r}"
    assert hashes == []


def test_explicit_x_session_id_header_passes_through():
    # We don't rewrite explicit headers — clients may be tracking their own
    # session correlation. The header value passes through verbatim.
    from kyde.server import _session_id

    sid, _ = _session_id(
        _FakeRequest({"X-Session-ID": "custom-client-id"}),
        [{"role": "user", "content": "hi"}],
    )
    assert sid == "custom-client-id"


def test_migration_0002_is_recorded():
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM schema_migrations "
                "WHERE version = '0002_session_id_normalize'"
            )
            assert (
                cur.fetchone() is not None
            ), "migration 0002_session_id_normalize was not recorded"


def test_migration_rewrites_legacy_session_ids():
    """End-to-end check: seed legacy IDs, run the migration SQL again,
    assert every legacy ID becomes a UUID and groupings are preserved."""
    # Use unique legacy IDs per test run so we don't clash with prior data.
    suffix = uuid.uuid4().hex[:8]
    legacy_ids = [
        f"s-{suffix}aaaaaa",
        f"s-{suffix}bbbbbb",
        f"session:{suffix}1234",
        f"session:{suffix}5678",
    ]

    # Seed rows directly into ledger + session_turns + dlp_alerts with the
    # legacy IDs. ledger.append() now mints UUIDs, so we side-step it.
    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            for sid in legacy_ids:
                cur.execute(
                    "INSERT INTO ledger ("
                    "  entry_id, timestamp, agent_id, action_type, model,"
                    "  input_hash, output_hash, prev_hash, entry_hash, signature,"
                    "  session_id"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        f"entry-{sid}",
                        now,
                        "agent:test",
                        "chat",
                        "gpt-4o-mini",
                        "0" * 64,
                        "0" * 64,
                        "0" * 64,
                        "0" * 64,
                        "stub",
                        sid,
                    ),
                )
                cur.execute(
                    "INSERT INTO session_turns (session_id, turn_hash, first_seen)"
                    " VALUES (%s, %s, %s)",
                    (sid, f"hash-{sid}"[:64], now),
                )
        conn.commit()

    # Re-apply the migration body (the 0002 file is forward-only, already
    # recorded; we run its rewrite SQL again to exercise it on fresh data).
    sql = (
        ledger.__file__.rsplit("/", 1)[0]
        + "/migrations/sql/0002_session_id_normalize.sql"
    )
    with open(sql, encoding="utf-8") as f:
        rewrite_sql = f.read()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(rewrite_sql)
        conn.commit()

    # Every seeded legacy ID should now be replaced with a UUID across
    # ledger, session_turns. Same legacy → same UUID across tables.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            for sid in legacy_ids:
                cur.execute(
                    "SELECT session_id FROM ledger WHERE entry_id = %s",
                    (f"entry-{sid}",),
                )
                row = cur.fetchone()
                assert row is not None
                new_sid = row["session_id"]
                assert _UUID_RE.match(new_sid), (
                    f"ledger row for legacy {sid!r} still has non-UUID " f"{new_sid!r}"
                )

                cur.execute(
                    "SELECT session_id FROM session_turns WHERE turn_hash = %s",
                    (f"hash-{sid}"[:64],),
                )
                t_row = cur.fetchone()
                assert t_row is not None
                assert (
                    t_row["session_id"] == new_sid
                ), "session_turns row was not rewritten to the same UUID"
