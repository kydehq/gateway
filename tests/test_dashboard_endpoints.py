"""Control-plane endpoint tests for the dashboard (kyde.dashboard).

test_dashboard_api.py covers the auth core (setup / login / lockout / user
CRUD). This file covers the admin/operator control surface that was still
dark: settings, DLP allow-list rules, the DLP-alert triage HTTP layer,
self-service profile, the SMTP test-send, and user unlock — including the
RBAC gates (admin-only vs any-authenticated) on each.

Everything runs through the real FastAPI app + auth middleware via the
`client` fixture, so the session cookie and role gates are exercised end
to end. SMTP egress is mocked; the rest hits the test DB.

NOTE: conftest's clean_db does NOT truncate the `settings` table, so the
autouse fixture resets every key these tests write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kyde import auth, crypto, ledger, settings as settings_module, smtp_sender

PASSWORD = "CorrectHorse!Battery9"

_TOUCHED_SETTINGS = [
    "DLP_BERT_THRESHOLD",
    "SMTP_PASSWORD_ENC",
    "SMTP_ENABLED",
    "SMTP_HOST",
    "SMTP_FROM_ADDRESS",
]


@pytest.fixture(autouse=True)
def _isolate_settings():
    for k in _TOUCHED_SETTINGS:
        ledger.delete_setting(k)
    settings_module.invalidate_cache()
    yield
    for k in _TOUCHED_SETTINGS:
        ledger.delete_setting(k)
    settings_module.invalidate_cache()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_user(username: str, roles: list[str], *, email: str | None = None) -> dict:
    return ledger.create_user(
        username=username,
        email=email if email is not None else f"{username}@example.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=roles,
        must_change_password=False,
    )


def _login_as(client, username: str) -> None:
    resp = client.post(
        "/login",
        data={"username": username, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _admin(client, username: str = "admin") -> dict:
    """Seed an admin and authenticate the client as them."""
    u = _seed_user(username, ["admin"])
    _login_as(client, username)
    return u


def _seed_alert(entry="e1", session="s1", pattern="aws_key") -> dict:
    row, is_new = ledger.upsert_dlp_alert(
        entry,
        session,
        "regex",
        0.9,
        [{"entity_type": pattern, "text": f"{pattern}-VAL", "severity": "HIGH"}],
    )
    assert is_new
    return row


# ===========================================================================
# Settings
# ===========================================================================


def test_settings_list_requires_admin(client):
    _seed_user("admin0", ["admin"])  # bootstrap so middleware passes
    _seed_user("vic", ["viewer"])
    _login_as(client, "vic")
    assert client.get("/api/settings").status_code == 403


def test_settings_list_unauthenticated_is_401(client):
    _seed_user("admin0", ["admin"])  # admin exists → not setup_required
    assert client.get("/api/settings").status_code == 401


def test_settings_list_returns_specs_with_redaction(client):
    _admin(client)
    # Seed a redacted secret so we can confirm it never echoes back.
    settings_module.set_value("SMTP_PASSWORD_ENC", "ciphertext-blob", None)
    items = {i["key"]: i for i in client.get("/api/settings").json()}
    assert "DLP_BERT_THRESHOLD" in items
    secret = items["SMTP_PASSWORD_ENC"]
    assert secret["value"] == ""  # never returned
    assert secret["is_set"] is True


def test_settings_patch_persists_value(client):
    _admin(client)
    r = client.patch("/api/settings/DLP_BERT_THRESHOLD", json={"value": "0.42"})
    assert r.status_code == 200
    assert settings_module.get_with_source("DLP_BERT_THRESHOLD") == (0.42, "db")


def test_settings_patch_unknown_key_404(client):
    _admin(client)
    r = client.patch("/api/settings/NOPE", json={"value": "x"})
    assert r.status_code == 404


def test_settings_patch_missing_value_400(client):
    _admin(client)
    r = client.patch("/api/settings/DLP_BERT_THRESHOLD", json={})
    assert r.status_code == 400


def test_settings_patch_validation_error_400(client):
    _admin(client)
    r = client.patch("/api/settings/DLP_BERT_THRESHOLD", json={"value": "1.5"})
    assert r.status_code == 400
    assert "between 0.0 and 1.0" in r.json()["error"]


def test_settings_patch_redacted_encrypts_and_hides(client, monkeypatch):
    _admin(client)
    monkeypatch.setattr(crypto, "encrypt", lambda s: f"ENC:{s}")
    r = client.patch("/api/settings/SMTP_PASSWORD_ENC", json={"value": "hunter2"})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == "" and body["is_set"] is True
    # The stored value is the ciphertext, not the plaintext.
    assert ledger.get_setting("SMTP_PASSWORD_ENC")["value"] == "ENC:hunter2"


def test_settings_patch_redacted_blank_is_no_change(client):
    _admin(client)
    settings_module.set_value("SMTP_PASSWORD_ENC", "existing-cipher", None)
    r = client.patch("/api/settings/SMTP_PASSWORD_ENC", json={"value": ""})
    assert r.status_code == 200
    assert r.json()["is_set"] is True
    # Unchanged.
    assert ledger.get_setting("SMTP_PASSWORD_ENC")["value"] == "existing-cipher"


def test_settings_reset_clears_override(client):
    _admin(client)
    settings_module.set_value("DLP_BERT_THRESHOLD", "0.42", None)
    r = client.delete("/api/settings/DLP_BERT_THRESHOLD")
    assert r.status_code == 200
    assert r.json()["source"] == "default"
    assert settings_module.get_with_source("DLP_BERT_THRESHOLD")[1] == "default"


def test_settings_reset_unknown_key_404(client):
    _admin(client)
    assert client.delete("/api/settings/NOPE").status_code == 404


# ===========================================================================
# SMTP test-send
# ===========================================================================


def test_smtp_test_disabled_returns_400(client):
    _admin(client)
    settings_module.set_value("SMTP_ENABLED", "false", None)
    r = client.post("/api/settings/smtp/test")
    assert r.status_code == 400
    assert "SMTP_ENABLED" in r.json()["error"]


def test_smtp_test_invalid_config_returns_400(client):
    _admin(client)
    settings_module.set_value("SMTP_ENABLED", "true", None)
    # No SMTP_HOST → load_smtp_config raises ValueError.
    r = client.post("/api/settings/smtp/test")
    assert r.status_code == 400


def test_smtp_test_no_auditors_returns_400(client):
    _admin(client)
    settings_module.set_value("SMTP_ENABLED", "true", None)
    settings_module.set_value("SMTP_HOST", "smtp.test", None)
    settings_module.set_value("SMTP_FROM_ADDRESS", "a@x.test", None)
    r = client.post("/api/settings/smtp/test")
    assert r.status_code == 400
    assert r.json()["recipients"] == 0


def test_smtp_test_success(client, monkeypatch):
    _admin(client)
    _seed_user("auditor1", ["auditor"], email="auditor1@x.test")
    settings_module.set_value("SMTP_ENABLED", "true", None)
    settings_module.set_value("SMTP_HOST", "smtp.test", None)
    settings_module.set_value("SMTP_FROM_ADDRESS", "a@x.test", None)
    send = AsyncMock()
    monkeypatch.setattr(smtp_sender, "send_test_email", send)
    r = client.post("/api/settings/smtp/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recipients": 1}
    send.assert_awaited_once()


# ===========================================================================
# DLP allow-list rules
# ===========================================================================


def test_dlp_rules_requires_admin(client):
    _seed_user("admin0", ["admin"])
    _seed_user("vic", ["viewer"])
    _login_as(client, "vic")
    assert client.get("/api/dlp-rules").status_code == 403


def test_dlp_rules_create_and_list(client):
    _admin(client)
    r = client.post(
        "/api/dlp-rules",
        json={"scanner": "regex", "entity_type": "email_address", "note": "noisy"},
    )
    assert r.status_code == 200
    rules = client.get("/api/dlp-rules").json()
    assert any(rule["entity_type"] == "email_address" for rule in rules)


def test_dlp_rules_create_rejects_non_allow_kind(client):
    _admin(client)
    r = client.post("/api/dlp-rules", json={"kind": "block", "entity_type": "x"})
    assert r.status_code == 400
    assert "allow" in r.json()["error"]


def test_dlp_rules_create_missing_entity_type_400(client):
    _admin(client)
    r = client.post("/api/dlp-rules", json={"scanner": "regex"})
    assert r.status_code == 400


def test_dlp_rules_create_duplicate_409(client):
    _admin(client)
    body = {"scanner": "regex", "entity_type": "ssn"}
    assert client.post("/api/dlp-rules", json=body).status_code == 200
    r = client.post("/api/dlp-rules", json=body)
    assert r.status_code == 409


def test_dlp_rules_delete(client):
    _admin(client)
    created = client.post(
        "/api/dlp-rules", json={"scanner": "regex", "entity_type": "phone"}
    ).json()
    r = client.delete(f"/api/dlp-rules/{created['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_dlp_rules_delete_unknown_404(client):
    _admin(client)
    assert client.delete("/api/dlp-rules/999999").status_code == 404


def test_dlp_rules_reapply_returns_summary(client):
    _admin(client)
    r = client.post("/api/dlp-rules/reapply")
    assert r.status_code == 200
    assert set(r.json()) >= {
        "scanned",
        "fully_allowlisted",
        "partially_updated",
        "unchanged",
    }


# ===========================================================================
# DLP alert triage HTTP layer
# ===========================================================================


def test_dlp_alerts_list(client):
    _admin(client)
    _seed_alert()
    alerts = client.get("/api/dlp-alerts").json()
    assert len(alerts) == 1


def test_dlp_alert_get_unknown_404(client):
    _admin(client)
    assert client.get("/api/dlp-alerts/does-not-exist").status_code == 404


def test_dlp_alert_get_by_id(client):
    _admin(client)
    alert = _seed_alert()
    r = client.get(f"/api/dlp-alerts/{alert['alert_id']}")
    assert r.status_code == 200
    assert r.json()["alert_id"] == alert["alert_id"]


def test_dlp_alert_transition_success(client):
    _admin(client)
    alert = _seed_alert()
    r = client.post(
        f"/api/dlp-alerts/{alert['alert_id']}/transition",
        json={"to_status": "in_review"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in_review"


def test_dlp_alert_transition_missing_to_status_400(client):
    _admin(client)
    alert = _seed_alert()
    r = client.post(f"/api/dlp-alerts/{alert['alert_id']}/transition", json={})
    assert r.status_code == 400


def test_dlp_alert_transition_unknown_alert_404(client):
    _admin(client)
    r = client.post("/api/dlp-alerts/nope/transition", json={"to_status": "in_review"})
    assert r.status_code == 404


def test_dlp_alert_transition_invalid_disposition_400(client):
    _admin(client)
    alert = _seed_alert()
    # Closing requires a disposition; omitting it is an invalid transition.
    r = client.post(
        f"/api/dlp-alerts/{alert['alert_id']}/transition",
        json={"to_status": "closed"},
    )
    assert r.status_code == 400


def test_dlp_alert_events_after_transition(client):
    _admin(client)
    alert = _seed_alert()
    client.post(
        f"/api/dlp-alerts/{alert['alert_id']}/transition",
        json={"to_status": "in_review"},
    )
    events = client.get(f"/api/dlp-alerts/{alert['alert_id']}/events").json()
    assert len(events) == 1
    assert events[0]["to_status"] == "in_review"


# ===========================================================================
# Read-only KPI / config snapshots
# ===========================================================================


def test_metrics_snapshot(client):
    _admin(client)
    ledger.append(
        agent_id="agent:m",
        action_type="chat",
        model="gpt-4o",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[],
        tool_calls=[],
        request_kind="chat",
    )
    body = client.get("/api/metrics").json()
    assert body["total_entries"] == 1
    assert "signature_success_rate" in body
    assert body["chain_integrity"]["valid"] in (True, False)
    assert "uptime_seconds" in body


def test_configuration_snapshot(client):
    _admin(client)
    body = client.get("/api/configuration").json()
    assert body["ledger_backend"] == "postgres"
    assert body["edition"] in ("enterprise", "starter")
    assert "signing_enabled" in body
    # The upstream routing table is surfaced for the admin UI.
    names = {u["name"] for u in body["configured_upstreams"]}
    assert "openai" in names


def test_change_password_page_self_service(client):
    _admin(client)
    html = client.get("/change-password").text
    # Non-forced path shows the current-password input field.
    assert "Enter current password" in html


def test_change_password_page_forced(client):
    _seed_user("admin0", ["admin"])  # bootstrap
    ledger.create_user(
        username="mustchange",
        email="mc@x.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=["viewer"],
        must_change_password=True,
    )
    _login_as(client, "mustchange")
    html = client.get("/change-password").text
    # Forced first-login path omits the current-password input field.
    assert "Enter current password" not in html


# ===========================================================================
# Self-service profile
# ===========================================================================


def test_profile_email_update(client):
    admin = _admin(client, "admin")
    r = client.post("/api/profile/email", json={"email": "new@kyde.test"})
    assert r.status_code == 204
    assert ledger.get_user_by_id(admin["id"])["email"] == "new@kyde.test"


def test_profile_email_invalid_400(client):
    _admin(client)
    r = client.post("/api/profile/email", json={"email": "not-an-email"})
    assert r.status_code == 400


def test_profile_password_change_success(client):
    admin = _admin(client)
    r = client.post(
        "/api/profile/password",
        json={"current_password": PASSWORD, "new_password": "BrandNew!Pass9xyz"},
    )
    assert r.status_code == 204
    # The new password verifies against the stored hash.
    stored = ledger.get_password_hash(admin["id"])
    assert auth.verify_password("BrandNew!Pass9xyz", stored)


def test_profile_password_wrong_current_400(client):
    _admin(client)
    r = client.post(
        "/api/profile/password",
        json={"current_password": "wrong", "new_password": "BrandNew!Pass9xyz"},
    )
    assert r.status_code == 400
    assert "incorrect" in r.json()["error"]


def test_profile_password_weak_new_400(client):
    _admin(client)
    r = client.post(
        "/api/profile/password",
        json={"current_password": PASSWORD, "new_password": "weak"},
    )
    assert r.status_code == 400


# ===========================================================================
# User unlock (admin)
# ===========================================================================


def test_unlock_user_clears_lockout(client):
    _admin(client)
    victim = _seed_user("locky", ["viewer"])
    # Trip the lockout with repeated bad logins (separate client cookie jar
    # not needed — /login is public and stateless w.r.t. the admin session).
    for _ in range(ledger.LOCKOUT_THRESHOLD):
        client.post(
            "/login",
            data={"username": "locky", "password": "wrong!X9pass"},
            follow_redirects=False,
        )
    assert ledger.get_user_by_id(victim["id"])["locked"] is True

    # Re-authenticate as admin (the bad logins above replaced the cookie).
    _login_as(client, "admin")
    r = client.post(f"/api/users/{victim['id']}/unlock")
    assert r.status_code == 204
    assert ledger.get_user_by_id(victim["id"])["locked"] is False


def test_unlock_unknown_user_404(client):
    _admin(client)
    assert client.post("/api/users/999999/unlock").status_code == 404


def test_unlock_requires_admin(client):
    _seed_user("admin0", ["admin"])
    _seed_user("vic", ["viewer"])
    _login_as(client, "vic")
    assert client.post("/api/users/1/unlock").status_code == 403
