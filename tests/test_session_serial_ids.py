"""
Tests for session serial_id wiring (Item 2 in the UI follow-up plan).

The frontend formats `SES-####` over an integer; rendering the UUID
session_id directly produces unusable output. These tests verify:

  1. The `sessions` table is populated by trigger on ledger insert.
  2. serial_id is monotonic and stable per session_id.
  3. /api/sessions exposes serial_id on each summary.
  4. /api/sessions/{id} exposes the session's serial_id.
  5. /api/dlp-alerts exposes serial_id as an alias for the BIGSERIAL id.
"""

import uuid

import pytest

from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


@pytest.fixture
def auditor_client(client):
    """Client logged in as an admin (admins have auditor privileges too)."""
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


def _append(session_id: str, agent_id: str = "agent:test") -> ledger.LedgerEntry:
    return ledger.append(
        agent_id=agent_id,
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
        session_id=session_id,
    )


def test_sessions_table_populated_by_trigger():
    sid = str(uuid.uuid4())
    _append(sid)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, serial_id, first_seen, last_seen"
                "  FROM sessions WHERE session_id = %s",
                (sid,),
            )
            row = cur.fetchone()

    assert row is not None
    assert row["session_id"] == sid
    assert isinstance(row["serial_id"], int) and row["serial_id"] > 0
    # First insert sets both timestamps equal.
    assert row["first_seen"] == row["last_seen"]


def test_serial_id_is_stable_per_session():
    sid = str(uuid.uuid4())
    _append(sid)
    serial_before = ledger.get_session_serial_id(sid)

    # Subsequent appends bump last_seen but must not change serial_id.
    _append(sid)
    _append(sid)
    serial_after = ledger.get_session_serial_id(sid)

    assert serial_before == serial_after


def test_serial_ids_are_monotonic_across_sessions():
    s_a = str(uuid.uuid4())
    s_b = str(uuid.uuid4())
    _append(s_a)
    _append(s_b)

    sa = ledger.get_session_serial_id(s_a)
    sb = ledger.get_session_serial_id(s_b)
    assert sa is not None and sb is not None
    assert sb > sa


def test_empty_session_id_does_not_create_sessions_row():
    _append("")

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM sessions WHERE session_id = ''")
            assert cur.fetchone()["c"] == 0


def test_api_sessions_exposes_serial_id(auditor_client):
    client = auditor_client
    sid = str(uuid.uuid4())
    _append(sid)

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    body = resp.json()

    match = next((s for s in body["items"] if s["session_id"] == sid), None)
    assert match is not None
    assert isinstance(match["serial_id"], int)


def test_api_session_detail_exposes_serial_id(auditor_client):
    client = auditor_client
    sid = str(uuid.uuid4())
    _append(sid)
    expected = ledger.get_session_serial_id(sid)

    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["serial_id"] == expected


def test_api_dlp_alerts_exposes_serial_id_alias(auditor_client):
    client = auditor_client
    # Seed a synthetic DLP alert directly — the dlp module's scan path needs
    # the sidecars, which the test harness doesn't run.
    sid = str(uuid.uuid4())
    entry = _append(sid)
    alert_id = f"alert-{sid}"
    import time

    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dlp_alerts ("
                "  alert_id, entry_id, session_id, scanner, score, findings,"
                "  status, created_at, updated_at, dedup_hash"
                ") VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s)",
                (
                    alert_id,
                    entry.entry_id,
                    sid,
                    "regex",
                    0.5,
                    "new",
                    now,
                    now,
                    alert_id,
                ),
            )
        conn.commit()

    resp = client.get("/api/dlp-alerts")
    assert resp.status_code == 200
    alerts = resp.json()
    match = next((a for a in alerts if a.get("alert_id") == alert_id), None)
    assert match is not None
    assert "serial_id" in match
    assert match["serial_id"] == match["id"]
