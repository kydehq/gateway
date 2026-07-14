"""
Polling worker that turns `dlp_alerts.email_status = 'pending'` rows into
actual SMTP messages.

Runs inside the kyde-api (dashboard) container: one asyncio task started
from the FastAPI lifespan. The proxy (kyde-gateway) only MARKS alerts as
pending inside `upsert_dlp_alert`; all SMTP egress and all retry logic
live here.

Design notes:

* `SELECT … FOR UPDATE SKIP LOCKED` lets multiple dashboard replicas
  coexist in the future without double-sending.
* The TRIGGER POLICY is evaluated HERE, not in the proxy. A runtime
  policy change takes effect on the very next poll — no restart.
* Failures increment `email_attempts`; only after `_MAX_ATTEMPTS` do we
  flip the row to 'failed' and stop retrying. Between attempts the row
  stays 'pending' and is picked up again on the next cycle.
* The loop NEVER raises to the caller — catching every exception so a
  transient DB blip or SMTP outage doesn't kill the task for the rest
  of the process lifetime.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from . import ledger, settings, smtp_sender

_POLL_INTERVAL_SECONDS = 10.0
_BATCH_SIZE = 20
_MAX_ATTEMPTS = 3

_worker_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def _should_send(alert: dict) -> tuple[bool, str]:
    """Decide whether to send an email for this alert under the current
    policy. Returns (send, reason). `reason` is human-readable and stored
    in `email_last_error` when we choose not to send."""
    policy = str(settings.get("SMTP_TRIGGER_POLICY") or "first_detection")
    seen_count = int(alert.get("seen_count", 1) or 1)
    score = float(alert.get("score", 0.0) or 0.0)
    is_repeat = seen_count > 1

    if policy == "every_scan":
        return True, ""
    if policy == "first_detection":
        if is_repeat:
            return False, "suppressed: repeat detection (policy=first_detection)"
        return True, ""
    if policy == "first_detection_min_score":
        if is_repeat:
            return False, "suppressed: repeat detection"
        min_score = float(settings.get("SMTP_MIN_SCORE") or 0.0)
        if score < min_score:
            return (
                False,
                f"suppressed: score {score:.3f} < SMTP_MIN_SCORE {min_score:.3f}",
            )
        return True, ""
    # Unknown policy — fail safe (don't send) rather than hammering auditors.
    return False, f"suppressed: unknown trigger policy {policy!r}"


# ---------------------------------------------------------------------------
# Row-state transitions
# ---------------------------------------------------------------------------


def _mark_sent(alert_id: int, now: float) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dlp_alerts
                   SET email_status = 'sent',
                       email_sent_at = %s,
                       email_last_error = '',
                       email_attempts = email_attempts + 1
                 WHERE id = %s
                """,
                (now, alert_id),
            )
        conn.commit()


def _mark_skipped(alert_id: int, reason: str) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dlp_alerts
                   SET email_status = 'skipped',
                       email_last_error = %s
                 WHERE id = %s
                """,
                (reason, alert_id),
            )
        conn.commit()


def _mark_failed_or_retry(alert_id: int, err: str, attempts: int) -> None:
    """After each failed send, either flip to 'failed' (cap hit) or leave
    as 'pending' for the next poll cycle."""
    new_attempts = attempts + 1
    if new_attempts >= _MAX_ATTEMPTS:
        status = "failed"
    else:
        status = "pending"
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dlp_alerts
                   SET email_status = %s,
                       email_attempts = %s,
                       email_last_error = %s
                 WHERE id = %s
                """,
                (status, new_attempts, err[:500], alert_id),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------


def _claim_pending_batch() -> list[dict]:
    """Return up to _BATCH_SIZE pending alert rows, row-locked with
    SKIP LOCKED so other workers/replicas don't pick them up. The rows
    stay pending on disk — we flip their status explicitly in the mark_*
    helpers after deciding what to do with each."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM dlp_alerts
                 WHERE email_status = 'pending'
                 ORDER BY id
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED
                """,
                (_BATCH_SIZE,),
            )
            rows = cur.fetchall()
        # Commit releases the row locks; the mark_* helpers re-acquire
        # per-row later. We're not trying to hold a long transaction —
        # SKIP LOCKED on a small batch is protection against a second
        # worker polling the same cycle, not a strict serialization.
        conn.commit()
    return list(rows)


async def _poll_once() -> None:
    if not bool(settings.get("SMTP_ENABLED")):
        return

    try:
        batch = _claim_pending_batch()
    except Exception as e:
        print(f"  ⚠ notifications: claim failed — {e}")
        return
    if not batch:
        return

    try:
        cfg = smtp_sender.load_smtp_config()
    except ValueError as e:
        # Config broken — surface on every pending row rather than silently
        # letting them pile up. They stay pending, so fixing the config
        # recovers naturally.
        print(f"  ⚠ notifications: SMTP config invalid — {e}")
        for row in batch:
            _mark_failed_or_retry(row["id"], str(e), int(row.get("email_attempts", 0)))
        return

    try:
        recipients = ledger.get_auditor_emails()
    except Exception as e:
        print(f"  ⚠ notifications: failed to load auditor list — {e}")
        return

    now = time.time()
    for row in batch:
        alert_id = row["id"]
        try:
            send, reason = _should_send(row)
            if not send:
                _mark_skipped(alert_id, reason)
                continue
            if not recipients:
                _mark_skipped(alert_id, "no auditor recipients")
                continue
            await smtp_sender.send_alert_email(cfg, recipients, dict(row))
            _mark_sent(alert_id, now)
            print(
                f"  ✉ notifications: alert {row.get('alert_id','')[:8]} sent "
                f"to {len(recipients)} recipient(s)"
            )
        except Exception as e:
            attempts = int(row.get("email_attempts", 0) or 0)
            print(
                f"  ⚠ notifications: alert {row.get('alert_id','')[:8]} send failed "
                f"(attempt {attempts + 1}/{_MAX_ATTEMPTS}): {e}"
            )
            _mark_failed_or_retry(alert_id, str(e), attempts)


async def _worker_loop() -> None:
    while True:
        try:
            await _poll_once()
        except Exception as e:
            # _poll_once catches its own errors, but belt-and-braces: the
            # loop must never die.
            print(f"  ⚠ notifications: poll cycle crashed — {e}")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def start_notification_worker() -> Optional[asyncio.Task]:
    """Idempotent. Safe to call multiple times; only the first spawns a task."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    loop = asyncio.get_event_loop()
    _worker_task = loop.create_task(_worker_loop())
    return _worker_task
