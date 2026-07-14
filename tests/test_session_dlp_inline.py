"""
Tests for inline DLP alerts attached to session-detail entries (Item 6).
"""

import time
import uuid


from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _append(session_id: str, agent_id: str = "agent:dlp-inline") -> ledger.LedgerEntry:
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


def _seed_admin(client) -> None:
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _seed_alert(entry_id: str, session_id: str, severity: str = "high") -> str:
    alert_id = f"alert-{uuid.uuid4().hex[:12]}"
    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dlp_alerts ("
                "  alert_id, entry_id, session_id, scanner, score, findings,"
                "  status, severity, created_at, updated_at, dedup_hash"
                ") VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s,%s)",
                (
                    alert_id,
                    entry_id,
                    session_id,
                    "regex",
                    0.8,
                    "new",
                    severity,
                    now,
                    now,
                    alert_id,
                ),
            )
        conn.commit()
    return alert_id


def test_session_detail_includes_alerts_per_entry(client):
    _seed_admin(client)
    sid = str(uuid.uuid4())
    e1 = _append(sid)
    e2 = _append(sid)
    a1 = _seed_alert(e1.entry_id, sid, severity="critical")
    a2 = _seed_alert(e1.entry_id, sid, severity="medium")
    _ = _seed_alert(e2.entry_id, sid, severity="low")

    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()

    entries = {e["entry_id"]: e for e in body["entries"]}
    assert {a["alert_id"] for a in entries[e1.entry_id]["dlp_alerts"]} == {a1, a2}
    # Severity surfaces so the UI can color the badge.
    severities = {a["severity"] for a in entries[e1.entry_id]["dlp_alerts"]}
    assert severities == {"critical", "medium"}

    assert len(entries[e2.entry_id]["dlp_alerts"]) == 1


def test_session_detail_no_alerts_returns_empty_list(client):
    _seed_admin(client)
    sid = str(uuid.uuid4())
    _append(sid)

    body = client.get(f"/api/sessions/{sid}").json()
    for e in body["entries"]:
        assert e["dlp_alerts"] == []
