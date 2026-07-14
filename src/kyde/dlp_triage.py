"""
DLP alert triage: lifecycle state machine + audit trail writer.

Two orthogonal concepts:
  * `status`      — lifecycle (where the alert is in triage).
  * `disposition` — outcome (what it turned out to be); set once at close.

The transition matrix below is the single source of truth for what a SOC
analyst is allowed to do with an alert. Every transition (and every
assignment / comment / tag change) is appended to `dlp_alert_events`, so
MTTR, dwell time, and per-analyst load are derivable from SQL.
"""

from __future__ import annotations

import time
from typing import Optional

from psycopg.types.json import Jsonb

from . import ledger

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

STATUSES: frozenset[str] = frozenset({"new", "in_review", "escalated", "closed"})

# Two human-facing dispositions plus two system-only ones. The system
# dispositions exist so backend automation (allowlist suppression and
# the dedupe CLI) can close alerts with an honest audit reason instead
# of stamping everything `false_positive`.
DISPOSITIONS: frozenset[str] = frozenset(
    {
        "false_positive",  # detector misfired (human call from UI)
        "confirmed_leak",  # real exfiltration (human call from UI)
        "allowlisted",  # suppressed by rule (system)
        "duplicate",  # rolled into another alert (system, via `dlp dedupe-alerts`)
    }
)

SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})

ACTOR_KINDS: frozenset[str] = frozenset({"user", "system"})

# Dispositions only a system actor may set. Used by `_mark_alert_allowlisted`
# and `dedupe-alerts` to close alerts non-interactively.
SYSTEM_DISPOSITIONS: frozenset[str] = frozenset({"allowlisted", "duplicate"})


# ---------------------------------------------------------------------------
# Transition matrix
# ---------------------------------------------------------------------------
# Closed is terminal. Authentication is enforced by the API layer; this
# module no longer differentiates analyst vs. lead — anyone who can call
# the transition endpoint can perform any of the three allowed moves.

# Status and disposition are independent axes:
#   - Status   = where in the process (open vs. terminal, normal vs. escalated)
#   - Disposition = what we concluded (set only on close)
# So `escalated → closed` accepts the same dispositions as `in_review → closed`
# — the verdict is stamped at close time, not at escalation time. De-escalation
# (`escalated → in_review`) exists for "the escalation itself was a false alarm".
_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("new", "in_review"),
        ("new", "escalated"),
        ("new", "closed"),
        ("in_review", "escalated"),
        ("in_review", "closed"),
        ("escalated", "in_review"),
        ("escalated", "closed"),
    }
)


class TransitionError(ValueError):
    """Raised for illegal state changes."""


def assert_allowed(from_status: str, to_status: str) -> None:
    """Raise TransitionError if this transition is not permitted."""
    if from_status not in STATUSES:
        raise TransitionError(f"unknown from_status: {from_status!r}")
    if to_status not in STATUSES:
        raise TransitionError(f"unknown to_status: {to_status!r}")
    if (from_status, to_status) not in _ALLOWED:
        raise TransitionError(f"transition {from_status} → {to_status} not allowed")


# ---------------------------------------------------------------------------
# Core helper: atomically transition an alert + append the event
# ---------------------------------------------------------------------------


def transition(
    *,
    alert_id: str,
    to_status: str,
    actor_kind: str = "user",
    actor_id: Optional[int] = None,
    disposition: Optional[str] = None,
    assignee_id: Optional[int] = None,
    note: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    """Validate and apply a lifecycle transition in a single transaction.

    On success, updates `dlp_alerts` AND appends one row to
    `dlp_alert_events`. On any validation failure nothing is written.

    Returns the updated alert row.
    """
    if actor_kind not in ACTOR_KINDS:
        raise TransitionError(f"unknown actor_kind: {actor_kind!r}")
    if disposition is not None and disposition not in DISPOSITIONS:
        raise TransitionError(f"unknown disposition: {disposition!r}")
    if to_status == "closed" and disposition is None:
        raise TransitionError("closing an alert requires a disposition")
    if to_status != "closed" and disposition is not None:
        raise TransitionError("disposition is only valid when closing")

    now = time.time()
    meta = Jsonb(metadata or {})

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            # Lock the row so two concurrent transitions can't both see the
            # same from_status and both succeed.
            cur.execute(
                "SELECT status, assignee_id FROM dlp_alerts "
                "WHERE alert_id = %s FOR UPDATE",
                (alert_id,),
            )
            current = cur.fetchone()
            if current is None:
                raise TransitionError(f"alert {alert_id!r} not found")

            from_status = current["status"]
            from_assignee = current["assignee_id"]

            assert_allowed(from_status, to_status)

            # The reviewer is recorded as the assignee on new → in_review;
            # close stamps closed_at. Reopen is not supported in the new
            # model, so reopen_count/reopened_at are intentionally not
            # touched here (legacy values on historical rows are preserved).
            is_claim = to_status == "in_review" and from_status == "new"
            is_close = to_status == "closed"

            cur.execute(
                """
                UPDATE dlp_alerts
                   SET status           = %s,
                       disposition      = %s,
                       disposition_note = CASE WHEN %s THEN %s ELSE disposition_note END,
                       assignee_id      = COALESCE(%s, assignee_id),
                       claimed_at       = CASE WHEN %s THEN %s ELSE claimed_at END,
                       closed_at        = CASE WHEN %s THEN %s ELSE closed_at END,
                       updated_at       = %s,
                       email_status     = CASE
                           WHEN %s AND email_status = 'pending' THEN 'skipped'
                           ELSE email_status
                       END
                 WHERE alert_id = %s
             RETURNING *
                """,
                (
                    to_status,
                    disposition,
                    is_close,
                    note,
                    assignee_id,
                    is_claim,
                    now,
                    is_close,
                    now,
                    now,
                    is_close,
                    alert_id,
                ),
            )
            updated = cur.fetchone()

            cur.execute(
                """
                INSERT INTO dlp_alert_events
                    (alert_id, actor_id, actor_kind, event_type,
                     from_status, to_status,
                     from_assignee, to_assignee,
                     disposition, note, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    alert_id,
                    actor_id,
                    actor_kind,
                    "status_change",
                    from_status,
                    to_status,
                    from_assignee,
                    assignee_id if assignee_id is not None else from_assignee,
                    disposition,
                    note,
                    meta,
                    now,
                ),
            )
        conn.commit()

    return updated or {}


def list_events(alert_id: str) -> list[dict]:
    """Return the full audit trail for an alert, oldest first."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM dlp_alert_events WHERE alert_id = %s "
                "ORDER BY created_at, id",
                (alert_id,),
            )
            return list(cur.fetchall())
