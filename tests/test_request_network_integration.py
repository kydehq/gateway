"""
Integration tests: ledger.append + ledger.record_request_network against
real Postgres. Verifies the full capture pipeline end-to-end:

- seq exposure on LedgerEntry (RETURNING seq round-trip)
- request_network row shape matches what the parser produced
- ledger.entry_hash is UNCHANGED by the side-table insert (chain invariant)
- historical backfill populates request_network for ledger rows created
  before the pipeline existed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from kyde import ledger, network_origin


# ---------------------------------------------------------------------------
# Minimal Request stand-in mirroring Starlette's surface.
# ---------------------------------------------------------------------------


class _Headers(dict):
    def __init__(self, data: dict[str, str]):
        super().__init__({k.lower(): v for k, v in data.items()})

    def get(self, key: str, default: str = "") -> str:
        return super().get(key.lower(), default)


@dataclass
class _Client:
    host: str


@dataclass
class _Request:
    headers: _Headers
    client: Optional[_Client]


def _make_request(**overrides: str) -> _Request:
    headers = {
        "X-Forwarded-For": overrides.pop("xff", "203.0.113.5, 10.0.0.5"),
        "User-Agent": overrides.pop("ua", "Cursor/0.42.3 (Macintosh)"),
    }
    for k, v in overrides.items():
        headers[k] = v
    peer = "10.0.0.6"
    return _Request(headers=_Headers(headers), client=_Client(host=peer))


def _append_simple(**overrides: Any) -> ledger.LedgerEntry:
    defaults = dict(
        agent_id="agent:test",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )
    defaults.update(overrides)
    return ledger.append(**defaults)


def _fetch_request_network(seq: int) -> Optional[dict]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM request_network WHERE seq = %s", (seq,))
            return cur.fetchone()


# ---------------------------------------------------------------------------
# seq exposure
# ---------------------------------------------------------------------------


def test_append_returns_seq():
    e = _append_simple()
    assert e.seq > 0


# ---------------------------------------------------------------------------
# record_request_network end-to-end
# ---------------------------------------------------------------------------


def test_record_request_network_full_roundtrip():
    req = _make_request()
    origin = network_origin.parse_from_request(
        req,
        upstream_url="https://api.openai.com/v1/chat/completions",
        trusted_cidrs=network_origin.parse_cidr_list("10.0.0.0/8,127.0.0.0/8"),
    )
    entry = _append_simple()
    ledger.record_request_network(entry.seq, entry.timestamp, origin)

    row = _fetch_request_network(entry.seq)
    assert row is not None
    assert row["origin_ip"] is not None
    # psycopg decodes INET to ipaddress.IPv4Address / IPv6Address objects.
    assert str(row["origin_ip"]) == "203.0.113.5"
    assert row["origin_class"] == "public"
    assert row["origin_subnet"] == "203.0.113.0/24"
    assert row["ua_tool"] == "cursor"
    assert row["ua_version"] == "0.42.3"
    assert row["upstream_host"] == "api.openai.com"
    # forwarded_chain is JSONB → native list of dicts
    assert isinstance(row["forwarded_chain"], list)
    assert len(row["forwarded_chain"]) >= 2  # at least client + peer
    assert row["forwarded_chain"][-1]["source"] == "peer"
    # timestamp is denormalized — must match the ledger row's timestamp exactly.
    assert row["timestamp"] == entry.timestamp


def test_record_request_network_is_idempotent_on_seq_collision():
    req = _make_request()
    origin = network_origin.parse_from_request(
        req,
        upstream_url="https://api.openai.com/",
        trusted_cidrs=network_origin.parse_cidr_list("10.0.0.0/8"),
    )
    entry = _append_simple()
    ledger.record_request_network(entry.seq, entry.timestamp, origin)
    # A second call with the same seq must not blow up (ON CONFLICT DO NOTHING).
    ledger.record_request_network(entry.seq, entry.timestamp, origin)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM request_network WHERE seq = %s",
                (entry.seq,),
            )
            assert cur.fetchone()["c"] == 1


def test_record_request_network_noop_on_invalid_seq():
    # seq=0 means "append failed" — no table write should happen.
    origin = network_origin.NetworkOrigin()
    ledger.record_request_network(0, 0.0, origin)
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM request_network")
            # This test runs under clean_db, so request_network should have
            # exactly 0 rows (no prior ledger rows → nothing to backfill).
            assert cur.fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# Hash-chain invariant: side-table writes don't touch the ledger row.
# ---------------------------------------------------------------------------


def test_side_table_insert_does_not_affect_entry_hash():
    # Baseline: append without touching request_network.
    baseline = _append_simple(agent_id="agent:one")
    baseline_hash = baseline.entry_hash

    # Sanity check: reading the row back must give the same entry_hash.
    fetched = ledger.get_entry(baseline.entry_id)
    assert fetched is not None
    assert fetched["entry_hash"] == baseline_hash

    # Now do a second append and attach a request_network row to it. The
    # earlier row's hash must not move, and the chain must remain valid.
    req = _make_request()
    origin = network_origin.parse_from_request(
        req,
        upstream_url="https://api.openai.com/",
        trusted_cidrs=network_origin.parse_cidr_list("10.0.0.0/8"),
    )
    second = _append_simple(agent_id="agent:two")
    ledger.record_request_network(second.seq, second.timestamp, origin)

    assert second.prev_hash == baseline_hash
    valid, errors = ledger.verify_chain()
    assert valid, errors


# ---------------------------------------------------------------------------
# Historical backfill: ledger rows inserted BEFORE request_network exists
# still get a backfilled side-table row on schema (re-)init.
# ---------------------------------------------------------------------------


def test_backfill_populates_request_network_for_existing_rows():
    # Append some rows WITHOUT calling record_request_network, then drop any
    # auto-inserted side-table rows to simulate a pre-feature deployment.
    e1 = _append_simple(agent_id="a1", client_ip="10.4.0.17")
    e2 = _append_simple(agent_id="a2", client_ip="8.8.8.8")
    e3 = _append_simple(agent_id="a3", client_ip="")  # no IP → skipped

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM request_network WHERE seq IN (%s, %s, %s)",
                (e1.seq, e2.seq, e3.seq),
            )
        conn.commit()

    # Run the historical backfill explicitly. The baseline migration runs it
    # once at install time; operators can re-invoke it after restoring data.
    ledger.backfill_request_network()

    r1 = _fetch_request_network(e1.seq)
    r2 = _fetch_request_network(e2.seq)
    r3 = _fetch_request_network(e3.seq)

    assert r1 is not None
    assert str(r1["origin_ip"]) == "10.4.0.17"
    assert r1["origin_class"] == "rfc1918"
    assert r1["origin_subnet"] == "10.4.0.0/24"

    assert r2 is not None
    assert str(r2["origin_ip"]) == "8.8.8.8"
    assert r2["origin_class"] == "public"
    assert r2["origin_subnet"] == "8.8.8.0/24"

    # Empty client_ip → backfill WHERE clause filters it out.
    assert r3 is None


# ---------------------------------------------------------------------------
# UA backfill CLI command — populates ua_* columns from user_agent post-hoc.
# ---------------------------------------------------------------------------


def test_backfill_ua_fields_populates_parsed_fields():
    from kyde import commands

    # Seed rows that already have a request_network shell (via the schema-
    # init backfill) but ua_tool is still empty.
    e1 = _append_simple(
        agent_id="a1",
        client_ip="10.4.0.17",
        user_agent="Cursor/0.42.3 (Macintosh)",
    )
    e2 = _append_simple(
        agent_id="a2",
        client_ip="203.0.113.5",
        user_agent="anthropic-sdk-python/0.34.0",
    )

    # Populate the request_network side table from the fresh ledger rows.
    # In production, server.record_request_network() is called inline per
    # request; here we use the offline backfill to short-circuit that.
    ledger.backfill_request_network()

    # Sanity: schema backfill fills origin_* but ua_* stays empty (UA
    # parsing is Python-side, not SQL).
    assert _fetch_request_network(e1.seq)["ua_tool"] == ""
    assert _fetch_request_network(e2.seq)["ua_tool"] == ""

    commands._cmd_backfill_ua_fields()

    r1 = _fetch_request_network(e1.seq)
    r2 = _fetch_request_network(e2.seq)
    assert r1["ua_tool"] == "cursor"
    assert r1["ua_version"] == "0.42.3"
    assert r2["ua_tool"] == "anthropic-sdk"
    assert r2["ua_version"] == "0.34.0"
