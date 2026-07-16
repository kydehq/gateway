"""
Tests for self-verifying PDF export (Item 9).

These tests don't try to parse PDFs back into structured data — instead
they verify:
  (a) endpoints respond with application/pdf
  (b) the bundle's signature verifies against the stored public key
  (c) the unauthenticated path is blocked
"""

import pytest

from kyde import auth, ledger, pdf_export

_PASSWORD = "CorrectHorse!Battery9"


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


def _append():
    return ledger.append(
        agent_id="agent:pdf",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )


def _verify_bundle(bundle: pdf_export.PdfBundle) -> None:
    """The bundle's signature is over `{v:1, sha256:<hex>}` of the pre-block
    PDF bytes. We can re-verify against the public key on disk.

    Signing is an enterprise feature (kyde-enterprise); in the starter edition the
    module is absent and the bundle is unsigned, so skip the verification.
    Rendering still ran before this point, so the render path stays exercised.
    """
    signing = pytest.importorskip("kyde.signing")
    payload = {"v": 1, "sha256": bundle.sha256_hex}
    assert signing.verify_payload(payload, bundle.signature_b64) is True


def test_compliance_report_renders_signed_pdf():
    bundle = pdf_export.compliance_report(
        {
            "status": "COMPLIANT",
            "total_entries": 42,
            "chain_intact": True,
            "signature_failures": 0,
            "signature_alg": "Ed25519",
            "public_key_fingerprint": "abc123",
            "regulatory_mappings": [],
        }
    )
    assert bundle.pdf.startswith(b"%PDF-")
    assert len(bundle.pdf) > 1000
    _verify_bundle(bundle)


def test_audit_log_renders_with_entries():
    bundle = pdf_export.audit_log(
        {
            "filters": {"action": "chat"},
            "entries": [
                {
                    "seq": 1,
                    "dt": "2026-05-17 10:00:00",
                    "agent_id": "agent:abc",
                    "action_type": "chat",
                    "model": "gpt-4o",
                    "upstream": "openai",
                },
                {
                    "seq": 2,
                    "dt": "2026-05-17 10:05:00",
                    "agent_id": "agent:abc",
                    "action_type": "tool_call",
                    "model": "gpt-4o",
                    "upstream": "openai",
                },
            ],
            "total_count": 2,
        }
    )
    assert bundle.pdf.startswith(b"%PDF-")
    _verify_bundle(bundle)


def test_incident_report_renders_steps():
    bundle = pdf_export.incident_report(
        {
            "chain_label": "Test chain",
            "status": "BLOCKED",
            "incident_serial": "INC-0001",
            "steps": [
                {
                    "label": "Step A",
                    "status": "completed",
                    "agent_id": "agent:a",
                    "dt": "2026-05-17 10:00:00",
                },
                {
                    "label": "Step B",
                    "status": "blocked",
                    "agent_id": "agent:b",
                    "dt": "2026-05-17 10:01:00",
                },
            ],
            "notes": "Blocked by DLP",
        }
    )
    assert bundle.pdf.startswith(b"%PDF-")
    _verify_bundle(bundle)


def test_compliance_evidence_renders_with_rows():
    bundle = pdf_export.compliance_evidence(
        {
            "title": "Session evidence",
            "subject": "Session 00000000-0000-4000-8000-000000000001",
            "rows": [("Session ID", "x"), ("Serial", "SES-0001"), ("Entry count", 3)],
            "entries": [
                {
                    "seq": 1,
                    "dt": "2026-05-17 10:00:00",
                    "action_type": "chat",
                    "model": "gpt-4o",
                },
            ],
        }
    )
    assert bundle.pdf.startswith(b"%PDF-")
    _verify_bundle(bundle)


def test_api_export_compliance_report(client):
    _seed_admin(client)
    _append()
    r = client.post("/api/export/compliance-report")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers.get("x-kyde-signed") == "1"
    assert r.content.startswith(b"%PDF-")


def test_api_export_audit_log(client):
    _seed_admin(client)
    _append()
    _append()

    r = client.post("/api/export/audit-log", json={})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_api_export_compliance_evidence_session(client):
    _seed_admin(client)
    ledger.append(
        agent_id="agent:ev",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
        session_id="00000000-0000-4000-8000-000000000099",
    )
    r = client.post(
        "/api/export/compliance-evidence",
        json={"kind": "session", "id": "00000000-0000-4000-8000-000000000099"},
    )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_api_export_compliance_evidence_validates_kind(client):
    _seed_admin(client)
    r = client.post("/api/export/compliance-evidence", json={"kind": "nope", "id": "x"})
    assert r.status_code == 400


def test_api_export_incident_report(client):
    _seed_admin(client)
    r = client.post(
        "/api/export/incident-report",
        json={
            "chain_label": "Demo chain",
            "status": "BLOCKED",
            "incident_serial": "INC-0042",
            "steps": [
                {
                    "label": "Read CRM",
                    "status": "completed",
                    "agent_id": "agent:a",
                    "dt": "2026-05-17 10:00:00",
                },
            ],
        },
    )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_export_endpoints_require_auth(client):
    for path in (
        "/api/export/compliance-report",
        "/api/export/audit-log",
        "/api/export/compliance-evidence",
        "/api/export/incident-report",
    ):
        r = client.post(path, json={})
        assert r.status_code == 401, f"expected 401 for {path}"
