"""Tests for the DLP-alert notification worker (kyde.notifications).

The worker turns `dlp_alerts.email_status='pending'` rows into SMTP sends,
applying the trigger policy and the retry/cap state machine. SMTP egress
(`smtp_sender.*`) and the auditor lookup are mocked; the alert rows and
their status transitions are exercised against the real test DB.

Covered:
  * `_should_send` — the full trigger-policy matrix.
  * `_mark_sent` / `_mark_skipped` / `_mark_failed_or_retry` — the row-state
    transitions, including the attempts-cap flip to 'failed'.
  * `_claim_pending_batch` — only pending rows, ordered.
  * `_poll_once` — disabled short-circuit, empty batch, broken config,
    no-auditors, policy-suppressed, success, and send-failure retry.
  * `start_notification_worker` — idempotency.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from kyde import ledger, notifications, smtp_sender
from kyde.smtp_sender import SmtpConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(pattern="aws_key") -> dict:
    # The regex dedup hash keys on entity_type + text (not pattern_id /
    # matched_value), so vary those to make distinct alerts distinct.
    return {"entity_type": pattern, "text": f"{pattern}-VAL", "severity": "HIGH"}


def _seed_pending(entry_id="e1", session_id="s1", score=0.9, pattern="aws_key") -> dict:
    row, is_new = ledger.upsert_dlp_alert(
        entry_id, session_id, "regex", score, [_finding(pattern)]
    )
    assert is_new and row["email_status"] == "pending"
    return row


def _alert_row(alert_db_id: int) -> dict:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM dlp_alerts WHERE id = %s", (alert_db_id,))
            return cur.fetchone()


def _settings_stub(values: dict):
    def fake_get(key):
        return values.get(key)

    return fake_get


def _dummy_cfg() -> SmtpConfig:
    return SmtpConfig(
        host="smtp.test",
        port=587,
        encryption="starttls",
        username="",
        password="",
        from_address="a@x.test",
        from_name="Kyde",
        reply_to="",
        tls_verify=True,
        timeout_seconds=10,
    )


@pytest.fixture(autouse=True)
def _reset_worker():
    notifications._worker_task = None
    yield
    notifications._worker_task = None


# ---------------------------------------------------------------------------
# _should_send — trigger policy matrix
# ---------------------------------------------------------------------------


def test_should_send_every_scan_always_sends(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_TRIGGER_POLICY": "every_scan"}),
    )
    send, _ = notifications._should_send({"seen_count": 5, "score": 0.1})
    assert send is True


def test_should_send_first_detection_new(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_TRIGGER_POLICY": "first_detection"}),
    )
    send, _ = notifications._should_send({"seen_count": 1, "score": 0.9})
    assert send is True


def test_should_send_first_detection_repeat_suppressed(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_TRIGGER_POLICY": "first_detection"}),
    )
    send, reason = notifications._should_send({"seen_count": 2, "score": 0.9})
    assert send is False
    assert "repeat detection" in reason


def test_should_send_min_score_below_threshold(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub(
            {
                "SMTP_TRIGGER_POLICY": "first_detection_min_score",
                "SMTP_MIN_SCORE": 0.8,
            }
        ),
    )
    send, reason = notifications._should_send({"seen_count": 1, "score": 0.5})
    assert send is False
    assert "SMTP_MIN_SCORE" in reason


def test_should_send_min_score_above_threshold(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub(
            {
                "SMTP_TRIGGER_POLICY": "first_detection_min_score",
                "SMTP_MIN_SCORE": 0.8,
            }
        ),
    )
    send, _ = notifications._should_send({"seen_count": 1, "score": 0.95})
    assert send is True


def test_should_send_min_score_repeat_suppressed(monkeypatch):
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub(
            {
                "SMTP_TRIGGER_POLICY": "first_detection_min_score",
                "SMTP_MIN_SCORE": 0.1,
            }
        ),
    )
    send, reason = notifications._should_send({"seen_count": 3, "score": 0.95})
    assert send is False
    assert "repeat detection" in reason


def test_should_send_unknown_policy_fails_safe(monkeypatch):
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_TRIGGER_POLICY": "bogus"})
    )
    send, reason = notifications._should_send({"seen_count": 1, "score": 0.9})
    assert send is False
    assert "unknown trigger policy" in reason


# ---------------------------------------------------------------------------
# Row-state transitions
# ---------------------------------------------------------------------------


def test_mark_sent():
    alert = _seed_pending()
    notifications._mark_sent(alert["id"], 1_700_000_000.0)
    row = _alert_row(alert["id"])
    assert row["email_status"] == "sent"
    assert row["email_sent_at"] == 1_700_000_000.0
    assert row["email_attempts"] == 1


def test_mark_skipped():
    alert = _seed_pending()
    notifications._mark_skipped(alert["id"], "no auditor recipients")
    row = _alert_row(alert["id"])
    assert row["email_status"] == "skipped"
    assert row["email_last_error"] == "no auditor recipients"


def test_mark_failed_or_retry_stays_pending_below_cap():
    alert = _seed_pending()
    notifications._mark_failed_or_retry(alert["id"], "smtp timeout", attempts=0)
    row = _alert_row(alert["id"])
    assert row["email_status"] == "pending"  # retry next cycle
    assert row["email_attempts"] == 1


def test_mark_failed_or_retry_flips_to_failed_at_cap():
    alert = _seed_pending()
    # attempts=2 → new_attempts=3 == _MAX_ATTEMPTS → 'failed'.
    notifications._mark_failed_or_retry(alert["id"], "smtp down", attempts=2)
    row = _alert_row(alert["id"])
    assert row["email_status"] == "failed"
    assert row["email_attempts"] == 3


def test_mark_failed_truncates_long_error():
    alert = _seed_pending()
    notifications._mark_failed_or_retry(alert["id"], "x" * 999, attempts=0)
    row = _alert_row(alert["id"])
    assert len(row["email_last_error"]) == 500


# ---------------------------------------------------------------------------
# _claim_pending_batch
# ---------------------------------------------------------------------------


def test_claim_pending_batch_returns_only_pending():
    a1 = _seed_pending("e1", "s1", pattern="aws_key")
    a2 = _seed_pending("e2", "s2", pattern="email_address")
    # Flip one to sent so it should NOT be claimed.
    notifications._mark_sent(a2["id"], 1.0)

    batch = notifications._claim_pending_batch()
    ids = {r["id"] for r in batch}
    assert a1["id"] in ids
    assert a2["id"] not in ids


def test_claim_pending_batch_empty():
    assert notifications._claim_pending_batch() == []


# ---------------------------------------------------------------------------
# _poll_once
# ---------------------------------------------------------------------------


def test_poll_once_disabled_short_circuits(monkeypatch):
    alert = _seed_pending()
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_ENABLED": False})
    )
    # load_smtp_config must never be reached.
    monkeypatch.setattr(
        smtp_sender, "load_smtp_config", lambda: pytest.fail("should not load config")
    )
    asyncio.run(notifications._poll_once())
    assert _alert_row(alert["id"])["email_status"] == "pending"  # untouched


def test_poll_once_empty_batch_is_noop(monkeypatch):
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_ENABLED": True})
    )
    monkeypatch.setattr(
        smtp_sender, "load_smtp_config", lambda: pytest.fail("no rows → no config load")
    )
    asyncio.run(notifications._poll_once())  # no pending rows seeded


def test_poll_once_broken_config_marks_rows(monkeypatch):
    alert = _seed_pending()
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_ENABLED": True})
    )

    def bad_config():
        raise ValueError("SMTP_HOST is not configured")

    monkeypatch.setattr(smtp_sender, "load_smtp_config", bad_config)
    asyncio.run(notifications._poll_once())
    row = _alert_row(alert["id"])
    # First failure → stays pending (retry), error recorded.
    assert row["email_status"] == "pending"
    assert row["email_attempts"] == 1
    assert "SMTP_HOST" in row["email_last_error"]


def test_poll_once_no_auditors_skips(monkeypatch):
    alert = _seed_pending()
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_ENABLED": True, "SMTP_TRIGGER_POLICY": "every_scan"}),
    )
    monkeypatch.setattr(smtp_sender, "load_smtp_config", _dummy_cfg)
    monkeypatch.setattr(ledger, "get_auditor_emails", lambda: [])
    asyncio.run(notifications._poll_once())
    row = _alert_row(alert["id"])
    assert row["email_status"] == "skipped"
    assert "no auditor recipients" in row["email_last_error"]


def test_poll_once_policy_suppressed_skips(monkeypatch):
    # Repeat detection under first_detection → skipped before any send.
    alert = _seed_pending()
    _, is_new = ledger.upsert_dlp_alert(  # same finding again → dedup, seen_count=2
        "e1b", "s1", "regex", 0.9, [_finding("aws_key")]
    )
    assert is_new is False
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub(
            {"SMTP_ENABLED": True, "SMTP_TRIGGER_POLICY": "first_detection"}
        ),
    )
    monkeypatch.setattr(smtp_sender, "load_smtp_config", _dummy_cfg)
    monkeypatch.setattr(ledger, "get_auditor_emails", lambda: ["a@x.test"])
    send_mock = AsyncMock()
    monkeypatch.setattr(smtp_sender, "send_alert_email", send_mock)

    asyncio.run(notifications._poll_once())
    assert _alert_row(alert["id"])["email_status"] == "skipped"
    send_mock.assert_not_called()


def test_poll_once_success_marks_sent(monkeypatch):
    alert = _seed_pending()
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_ENABLED": True, "SMTP_TRIGGER_POLICY": "every_scan"}),
    )
    monkeypatch.setattr(smtp_sender, "load_smtp_config", _dummy_cfg)
    monkeypatch.setattr(ledger, "get_auditor_emails", lambda: ["a@x.test", "b@x.test"])
    send_mock = AsyncMock()
    monkeypatch.setattr(smtp_sender, "send_alert_email", send_mock)

    asyncio.run(notifications._poll_once())
    row = _alert_row(alert["id"])
    assert row["email_status"] == "sent"
    assert row["email_attempts"] == 1
    send_mock.assert_awaited_once()
    # Recipients forwarded to the sender.
    assert send_mock.await_args.args[1] == ["a@x.test", "b@x.test"]


def test_poll_once_send_failure_retries(monkeypatch):
    alert = _seed_pending()
    monkeypatch.setattr(
        notifications.settings,
        "get",
        _settings_stub({"SMTP_ENABLED": True, "SMTP_TRIGGER_POLICY": "every_scan"}),
    )
    monkeypatch.setattr(smtp_sender, "load_smtp_config", _dummy_cfg)
    monkeypatch.setattr(ledger, "get_auditor_emails", lambda: ["a@x.test"])

    async def boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(smtp_sender, "send_alert_email", boom)
    asyncio.run(notifications._poll_once())
    row = _alert_row(alert["id"])
    assert row["email_status"] == "pending"  # below cap → retry
    assert row["email_attempts"] == 1
    assert "connection reset" in row["email_last_error"]


def test_poll_once_claim_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_ENABLED": True})
    )
    monkeypatch.setattr(
        notifications,
        "_claim_pending_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    # Must return cleanly, not raise.
    asyncio.run(notifications._poll_once())


def test_poll_once_auditor_lookup_failure_is_swallowed(monkeypatch):
    _seed_pending()
    monkeypatch.setattr(
        notifications.settings, "get", _settings_stub({"SMTP_ENABLED": True})
    )
    monkeypatch.setattr(smtp_sender, "load_smtp_config", _dummy_cfg)
    monkeypatch.setattr(
        ledger,
        "get_auditor_emails",
        lambda: (_ for _ in ()).throw(RuntimeError("query failed")),
    )
    asyncio.run(notifications._poll_once())  # returns without raising


def test_worker_loop_swallows_poll_errors(monkeypatch):
    # The loop must survive a _poll_once exception and proceed to sleep;
    # we break out by having the (patched) sleep cancel.
    calls = []

    async def poll():
        calls.append(1)
        raise RuntimeError("poll boom")

    async def fake_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(notifications, "_poll_once", poll)
    monkeypatch.setattr(notifications.asyncio, "sleep", fake_sleep)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await notifications._worker_loop()

    asyncio.run(go())
    assert calls  # poll was attempted and its error did not kill the loop


# ---------------------------------------------------------------------------
# start_notification_worker
# ---------------------------------------------------------------------------


def test_start_worker_is_idempotent(monkeypatch):
    monkeypatch.setattr(notifications, "_poll_once", AsyncMock(return_value=None))

    async def go():
        t1 = notifications.start_notification_worker()
        t2 = notifications.start_notification_worker()
        assert t1 is t2  # second call returns the same live task
        t1.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
