"""
Tests for the CSV / JSON export endpoints.

Covers /api/export/audit-log-csv, /api/export/ledger-csv, and
/api/export/chain-signatures.
"""

import csv
import io
import json
import time

import pytest

from kyde import auth, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _seed_admin(client) -> None:
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],  # admin satisfies _is_auditor
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _seed_viewer(client) -> None:
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    ledger.create_user(
        username="viewer",
        email="viewer@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["viewer"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "viewer", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _append() -> ledger.LedgerEntry:
    return ledger.append(
        agent_id="agent:exp",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hello"}]},
        response_body={"choices": [{"message": {"content": "hi"}}]},
        why_messages=[{"role": "user", "content": "hello"}],
        tool_calls=[],
    )


# ---------------------------------------------------------------------------
# Audit Log CSV
# ---------------------------------------------------------------------------


def test_audit_log_csv_requires_auth(client):
    r = client.post("/api/export/audit-log-csv", json={})
    assert r.status_code == 401


def test_audit_log_csv_returns_csv_with_filtered_rows(client):
    _seed_admin(client)
    _append()
    _append()

    r = client.post("/api/export/audit-log-csv", json={"window": "24h"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")

    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    header = rows[0]
    data = rows[1:]
    assert "seq" in header
    assert "entry_id" in header
    assert "timestamp_iso" in header
    assert len(data) == 2


def test_audit_log_csv_honors_action_filter(client):
    _seed_admin(client)
    _append()
    ledger.append(
        agent_id="agent:exp",
        action_type="tool_call",
        model="gpt-4o-mini",
        request_body={},
        response_body={},
        why_messages=[],
        tool_calls=[{"function": "x"}],
    )

    r = client.post(
        "/api/export/audit-log-csv",
        json={"window": "24h", "action": "tool_call"},
    )
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    data = rows[1:]
    assert len(data) == 1
    action_idx = rows[0].index("action_type")
    assert data[0][action_idx] == "tool_call"


# ---------------------------------------------------------------------------
# Ledger CSV
# ---------------------------------------------------------------------------


def test_ledger_csv_requires_admin_or_auditor(client):
    _seed_viewer(client)
    r = client.post("/api/export/ledger-csv", json={"window": "24h"})
    assert r.status_code == 403


def test_ledger_csv_returns_chain_fields(client):
    _seed_admin(client)
    e = _append()

    r = client.post("/api/export/ledger-csv", json={"window": "24h"})
    assert r.status_code == 200
    assert "ledger" in r.headers.get("content-disposition", "")

    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    header = rows[0]
    # Chain integrity columns are present so the recipient can verify
    # without contacting the server again.
    for col in (
        "prev_hash",
        "entry_hash",
        "signature_b64",
        "input_hash",
        "output_hash",
    ):
        assert col in header

    # The row we just appended is in the export.
    eid_idx = header.index("entry_id")
    assert any(r[eid_idx] == e.entry_id for r in rows[1:])


def test_ledger_csv_window_all_returns_everything(client):
    _seed_admin(client)
    # Insert one very old row + one fresh row via direct SQL.
    old_ts = time.time() - 100 * 86400
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ledger ("
                " entry_id, timestamp, agent_id, action_type, model,"
                " input_hash, output_hash, prev_hash, entry_hash, signature,"
                " session_id, upstream"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "old-entry",
                    old_ts,
                    "agent:old",
                    "chat",
                    "gpt-4o",
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "0" * 64,
                    "stub",
                    "",
                    "openai",
                ),
            )
        conn.commit()
    _append()

    r = client.post("/api/export/ledger-csv", json={"window": "all"})
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    header = rows[0]
    eid_idx = header.index("entry_id")
    ids = {row[eid_idx] for row in rows[1:]}
    assert "old-entry" in ids


# ---------------------------------------------------------------------------
# Chain Signatures JSON
# ---------------------------------------------------------------------------


def test_chain_signatures_requires_admin_or_auditor(client):
    _seed_viewer(client)
    r = client.post("/api/export/chain-signatures", json={"window": "24h"})
    assert r.status_code == 403


def test_chain_signatures_returns_verifiable_payload(client):
    # Audit signing is an enterprise feature (kyde-enterprise); the chain-signatures
    # export only carries verifiable signatures when it's installed.
    signing = pytest.importorskip("kyde.signing")
    _seed_admin(client)
    e = _append()

    r = client.post("/api/export/chain-signatures", json={"window": "24h"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    body = json.loads(r.text)
    assert body["schema_version"] == 1
    assert body["format"] == "chain-signatures-v1"
    assert body["algorithm"] in ("Ed25519", "ECDSA-P256 (TPM)")
    assert isinstance(body["entries"], list)
    assert body["entry_count"] == len(body["entries"])

    # The exported signable should round-trip through canonical_bytes() +
    # SHA-256 to entry_hash, AND verify_payload should accept the signature.
    import hashlib

    target = next(x for x in body["entries"] if x["entry_id"] == e.entry_id)
    canon = signing.canonical_bytes(target["signable"])
    assert hashlib.sha256(canon).hexdigest() == target["entry_hash"]
    assert signing.verify_payload(target["signable"], target["signature_b64"]) is True


def test_chain_signatures_carries_public_key(client):
    pytest.importorskip("kyde.signing")  # enterprise feature — no public key in sandbox
    _seed_admin(client)
    _append()

    r = client.post("/api/export/chain-signatures", json={"window": "24h"})
    body = json.loads(r.text)
    # PEM-wrapped + base64'd so JSON tools don't choke on embedded newlines.
    assert isinstance(body["public_key_pem_b64"], str)
    assert len(body["public_key_pem_b64"]) > 0
