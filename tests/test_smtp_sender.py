"""Tests for the SMTP send path (kyde.smtp_sender).

`_dashboard_url` and the alert link are covered by test_alert_email_url.py.
This file covers the rest of the module — the parts that decide *what* goes
on the wire and *how* the TLS handshake is set up, with `aiosmtplib.send`
mocked so nothing leaves the process:

  * `load_smtp_config` — required-field validation + password decryption
    (success and the AES-key-rotated failure).
  * `send_email` — recipient guard, header assembly, and the three
    encryption modes mapping to the right aiosmtplib flags.
  * the alert template helpers (`_findings_summary`, `build_alert_subject`,
    `build_alert_bodies`) and the two senders.
"""

from __future__ import annotations

import asyncio

import pytest

from kyde import smtp_sender
from kyde.smtp_sender import SmtpConfig


def _cfg(**overrides) -> SmtpConfig:
    base = dict(
        host="smtp.test",
        port=587,
        encryption="starttls",
        username="user",
        password="secret",
        from_address="alerts@kyde.test",
        from_name="Kyde Alerts",
        reply_to="",
        tls_verify=True,
        timeout_seconds=10,
    )
    base.update(overrides)
    return SmtpConfig(**base)


# ---------------------------------------------------------------------------
# load_smtp_config
# ---------------------------------------------------------------------------


def _settings_stub(values: dict):
    def fake_get(key):
        return values.get(key, "")

    return fake_get


def test_load_config_requires_host(monkeypatch):
    monkeypatch.setattr(smtp_sender.settings, "get", _settings_stub({"SMTP_HOST": ""}))
    with pytest.raises(ValueError, match="SMTP_HOST"):
        smtp_sender.load_smtp_config()


def test_load_config_requires_from_address(monkeypatch):
    monkeypatch.setattr(
        smtp_sender.settings, "get", _settings_stub({"SMTP_HOST": "smtp.x"})
    )
    with pytest.raises(ValueError, match="SMTP_FROM_ADDRESS"):
        smtp_sender.load_smtp_config()


def test_load_config_decrypts_password(monkeypatch):
    values = {
        "SMTP_HOST": "smtp.x",
        "SMTP_FROM_ADDRESS": "a@x.test",
        "SMTP_PASSWORD_ENC": "ENCRYPTED_BLOB",
        "SMTP_PORT": 465,
        "SMTP_ENCRYPTION": "tls",
        "SMTP_USERNAME": "u",
        "SMTP_FROM_NAME": "",
        "SMTP_REPLY_TO": "",
        "SMTP_TLS_VERIFY": True,
        "SMTP_TIMEOUT_SECONDS": 10,
    }
    monkeypatch.setattr(smtp_sender.settings, "get", _settings_stub(values))
    monkeypatch.setattr(smtp_sender.crypto, "decrypt", lambda blob: "plaintext-pw")

    cfg = smtp_sender.load_smtp_config()
    assert cfg.password == "plaintext-pw"
    assert cfg.port == 465
    assert cfg.encryption == "tls"
    # Blank from_name falls back to the default brand name.
    assert cfg.from_name == "Kyde Gateway Alerts"


def test_load_config_decrypt_failure_raises_clear_error(monkeypatch):
    values = {
        "SMTP_HOST": "smtp.x",
        "SMTP_FROM_ADDRESS": "a@x.test",
        "SMTP_PASSWORD_ENC": "CORRUPT",
        "SMTP_PORT": 587,
        "SMTP_ENCRYPTION": "starttls",
        "SMTP_USERNAME": "",
        "SMTP_FROM_NAME": "",
        "SMTP_REPLY_TO": "",
        "SMTP_TLS_VERIFY": True,
        "SMTP_TIMEOUT_SECONDS": 10,
    }
    monkeypatch.setattr(smtp_sender.settings, "get", _settings_stub(values))

    def boom(blob):
        raise RuntimeError("bad key")

    monkeypatch.setattr(smtp_sender.crypto, "decrypt", boom)
    with pytest.raises(ValueError, match="could not be decrypted"):
        smtp_sender.load_smtp_config()


def test_load_config_no_password_blob_leaves_password_blank(monkeypatch):
    values = {
        "SMTP_HOST": "smtp.x",
        "SMTP_FROM_ADDRESS": "a@x.test",
        "SMTP_PASSWORD_ENC": "",
        "SMTP_PORT": 25,
        "SMTP_ENCRYPTION": "none",
        "SMTP_USERNAME": "",
        "SMTP_FROM_NAME": "Custom",
        "SMTP_REPLY_TO": "reply@x.test",
        "SMTP_TLS_VERIFY": False,
        "SMTP_TIMEOUT_SECONDS": 5,
    }
    monkeypatch.setattr(smtp_sender.settings, "get", _settings_stub(values))
    cfg = smtp_sender.load_smtp_config()
    assert cfg.password == ""
    assert cfg.from_name == "Custom"
    assert cfg.reply_to == "reply@x.test"
    assert cfg.tls_verify is False


# ---------------------------------------------------------------------------
# _tls_context
# ---------------------------------------------------------------------------


def test_tls_context_verify_on():
    import ssl

    ctx = smtp_sender._tls_context(True)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_tls_context_verify_off():
    import ssl

    ctx = smtp_sender._tls_context(False)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


def test_send_email_no_recipients_raises():
    with pytest.raises(ValueError, match="no recipients"):
        asyncio.run(smtp_sender.send_email(_cfg(), [], "subj", "body"))


def _capture_send(monkeypatch):
    sent = {}

    async def fake_send(msg, **kwargs):
        sent["msg"] = msg
        sent["kwargs"] = kwargs

    monkeypatch.setattr(smtp_sender.aiosmtplib, "send", fake_send)
    return sent


def test_send_email_assembles_headers(monkeypatch):
    sent = _capture_send(monkeypatch)
    asyncio.run(
        smtp_sender.send_email(
            _cfg(reply_to="sec@kyde.test"),
            ["a@x.test", "b@x.test"],
            "Subject Line",
            "text body",
            html_body="<p>html</p>",
            extra_headers={"References": "<thread@kyde>"},
        )
    )
    msg = sent["msg"]
    assert msg["Subject"] == "Subject Line"
    assert msg["To"] == "a@x.test, b@x.test"
    assert "Kyde Alerts" in msg["From"]
    assert msg["Reply-To"] == "sec@kyde.test"
    assert msg["References"] == "<thread@kyde>"


@pytest.mark.parametrize(
    "encryption,exp_use_tls,exp_start_tls,ctx_set",
    [
        ("tls", True, False, True),
        ("starttls", False, True, True),
        ("none", False, False, False),
    ],
)
def test_send_email_encryption_modes(
    monkeypatch, encryption, exp_use_tls, exp_start_tls, ctx_set
):
    sent = _capture_send(monkeypatch)
    asyncio.run(
        smtp_sender.send_email(_cfg(encryption=encryption), ["a@x.test"], "s", "b")
    )
    kw = sent["kwargs"]
    assert kw["use_tls"] is exp_use_tls
    assert kw["start_tls"] is exp_start_tls
    assert (kw["tls_context"] is not None) is ctx_set


def test_send_email_blank_credentials_passed_as_none(monkeypatch):
    sent = _capture_send(monkeypatch)
    asyncio.run(
        smtp_sender.send_email(_cfg(username="", password=""), ["a@x.test"], "s", "b")
    )
    assert sent["kwargs"]["username"] is None
    assert sent["kwargs"]["password"] is None


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def test_findings_summary_regex_dedupes_and_sorts():
    findings = [
        {"entity_type": "email_address"},
        {"entity_type": "aws_key"},
        {"entity_type": "email_address"},
    ]
    assert smtp_sender._findings_summary("regex", findings) == "aws_key, email_address"


def test_findings_summary_regex_falls_back_to_pattern_name():
    findings = [{"pattern_name": "SQL Injection"}]
    assert smtp_sender._findings_summary("regex", findings) == "SQL Injection"


def test_findings_summary_regex_empty():
    assert smtp_sender._findings_summary("regex", []) == "regex match"


def test_findings_summary_bert_uses_label():
    assert smtp_sender._findings_summary("bert", [{"label": "TOXICITY"}]) == "TOXICITY"


def test_findings_summary_bert_no_findings():
    assert smtp_sender._findings_summary("bert", []) == "classifier hit"


def test_findings_summary_unknown_scanner():
    assert smtp_sender._findings_summary("mystery", []) == "mystery"


def test_build_alert_subject():
    alert = {
        "scanner": "regex",
        "score": 0.873,
        "findings": [{"entity_type": "aws_key"}],
    }
    subj = smtp_sender.build_alert_subject(alert)
    assert subj == "[Kyde Alert] regex — aws_key (score 0.87)"


def test_build_alert_bodies_contains_key_fields(monkeypatch):
    monkeypatch.setattr(smtp_sender, "_dashboard_url", lambda: "https://kyde.test")
    alert = {
        "alert_id": "alert-uuid-123",
        "entry_id": "entry-abc",
        "session_id": "sess-xyz",
        "scanner": "bert",
        "score": 0.95,
        "seen_count": 3,
        "findings": [{"label": "PII"}],
        "created_at": 1_700_000_000.0,
    }
    text, html = smtp_sender.build_alert_bodies(alert)
    assert "alert-uuid-123" in text
    assert "Seen count    : 3" in text
    assert "https://kyde.test/alerts/alert-uuid-123" in text
    # HTML carries the same link and summary.
    assert "https://kyde.test/alerts/alert-uuid-123" in html
    assert "PII" in html


def test_build_alert_bodies_handles_missing_session():
    alert = {"alert_id": "a", "scanner": "regex", "findings": []}
    text, _html = smtp_sender.build_alert_bodies(alert)
    assert "(none)" in text


# ---------------------------------------------------------------------------
# send_alert_email / send_test_email
# ---------------------------------------------------------------------------


def test_send_alert_email_renders_and_sends(monkeypatch):
    captured = {}

    async def fake_send_email(
        cfg, recipients, subject, text_body, html_body=None, extra_headers=None
    ):
        captured.update(
            recipients=recipients,
            subject=subject,
            extra_headers=extra_headers,
        )

    monkeypatch.setattr(smtp_sender, "send_email", fake_send_email)
    alert = {
        "alert_id": "a1",
        "scanner": "regex",
        "score": 0.9,
        "findings": [{"entity_type": "aws_key"}],
    }
    asyncio.run(smtp_sender.send_alert_email(_cfg(), ["who@x.test"], alert))
    assert captured["recipients"] == ["who@x.test"]
    assert "aws_key" in captured["subject"]
    # Threading anchor wired into both References and In-Reply-To.
    assert captured["extra_headers"]["In-Reply-To"] == "<alert-a1@kyde>"


def test_send_test_email_canned(monkeypatch):
    captured = {}

    async def fake_send_email(
        cfg, recipients, subject, text_body, html_body=None, extra_headers=None
    ):
        captured.update(subject=subject, text=text_body)

    monkeypatch.setattr(smtp_sender, "send_email", fake_send_email)
    asyncio.run(
        smtp_sender.send_test_email(_cfg(host="relay.x", port=25), ["a@x.test"])
    )
    assert captured["subject"] == "[Kyde] SMTP test"
    assert "relay.x:25" in captured["text"]
