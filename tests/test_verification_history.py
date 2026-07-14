"""
Tests for the verification_runs audit log (Item 7).
"""

from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _append(agent_id: str = "agent:vh"):
    return ledger.append(
        agent_id=agent_id,
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )


def _seed_admin(client):
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


def test_verify_chain_records_a_pass_row():
    _append()
    valid, errors = ledger.verify_chain()
    assert valid is True

    runs = ledger.list_verification_runs(limit=5)
    assert len(runs) >= 1
    latest = runs[0]
    assert latest["status"] == "pass"
    assert latest["chain_breaks"] == 0
    assert latest["signature_failures"] == 0


def test_verify_chain_record_false_skips_persistence():
    before = len(ledger.list_verification_runs(limit=100))
    ledger.verify_chain(record=False)
    after = len(ledger.list_verification_runs(limit=100))
    assert before == after


def test_api_verification_runs_returns_history(client):
    _seed_admin(client)
    _append()
    ledger.verify_chain()
    ledger.verify_chain()

    resp = client.get("/api/verification-runs?limit=5")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 2
    assert {"run_id", "run_at", "status", "total_entries", "chain_breaks"}.issubset(
        rows[0]
    )


def test_api_verification_runs_requires_auth(client):
    assert client.get("/api/verification-runs").status_code == 401


def test_verify_endpoint_writes_run(client):
    _seed_admin(client)
    _append()
    before = len(ledger.list_verification_runs(limit=200))

    resp = client.get("/api/verify")
    assert resp.status_code == 200

    after = len(ledger.list_verification_runs(limit=200))
    assert after == before + 1
