"""Tests for the DLP alert triage state machine (kyde.dlp_triage).

Two layers:
  * `assert_allowed` — the pure transition matrix; the single source of
    truth for what a SOC analyst may do with an alert.
  * `transition` / `list_events` — the atomic "update alert + append
    audit event" path, exercised against the test DB.

Alerts are seeded via ledger.upsert_dlp_alert (status='new',
email_status='pending') so the transition behaviours — claim stamping,
disposition gating, email cancellation, audit-trail append — are tested
against real rows rather than mocks.
"""

from __future__ import annotations

import pytest

from kyde import auth, dlp_triage, ledger
from kyde.dlp_triage import TransitionError, assert_allowed


# ---------------------------------------------------------------------------
# Pure: enumerations + transition matrix
# ---------------------------------------------------------------------------


def test_system_dispositions_are_a_subset_of_dispositions():
    assert dlp_triage.SYSTEM_DISPOSITIONS <= dlp_triage.DISPOSITIONS


@pytest.mark.parametrize(
    "frm,to",
    sorted(dlp_triage._ALLOWED),
)
def test_allowed_transitions_pass(frm, to):
    # Every pair in the matrix must validate without raising.
    assert_allowed(frm, to)


@pytest.mark.parametrize(
    "frm,to",
    [
        ("closed", "in_review"),  # closed is terminal
        ("closed", "new"),
        ("new", "new"),  # no self-loops defined
        ("in_review", "new"),  # can't un-claim back to new
        ("escalated", "new"),
        ("in_review", "in_review"),
    ],
)
def test_disallowed_transitions_raise(frm, to):
    with pytest.raises(TransitionError):
        assert_allowed(frm, to)


def test_unknown_status_raises():
    with pytest.raises(TransitionError, match="from_status"):
        assert_allowed("bogus", "closed")
    with pytest.raises(TransitionError, match="to_status"):
        assert_allowed("new", "bogus")


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def _seed_analyst(username: str = "analyst") -> int:
    """Create a real user so assignee_id FKs resolve, return its id."""
    user = ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password("CorrectHorse!Battery9"),
        roles=["admin"],
        must_change_password=False,
    )
    return user["id"]


def _seed_alert(entry_id: str = "entry-1", session_id: str = "session-1") -> dict:
    row, is_new = ledger.upsert_dlp_alert(
        entry_id,
        session_id,
        "regex",
        0.95,
        [{"pattern_id": "aws_key", "matched_value": "AKIA...", "severity": "HIGH"}],
    )
    assert is_new
    assert row["status"] == "new"
    assert row["email_status"] == "pending"
    return row


# ---------------------------------------------------------------------------
# transition — validation guards (no DB write on failure)
# ---------------------------------------------------------------------------


def test_unknown_actor_kind_raises():
    alert = _seed_alert()
    with pytest.raises(TransitionError, match="actor_kind"):
        dlp_triage.transition(
            alert_id=alert["alert_id"], to_status="in_review", actor_kind="robot"
        )


def test_unknown_disposition_raises():
    alert = _seed_alert()
    with pytest.raises(TransitionError, match="disposition"):
        dlp_triage.transition(
            alert_id=alert["alert_id"], to_status="closed", disposition="meh"
        )


def test_closing_without_disposition_raises():
    alert = _seed_alert()
    with pytest.raises(TransitionError, match="requires a disposition"):
        dlp_triage.transition(alert_id=alert["alert_id"], to_status="closed")


def test_disposition_without_closing_raises():
    alert = _seed_alert()
    with pytest.raises(TransitionError, match="only valid when closing"):
        dlp_triage.transition(
            alert_id=alert["alert_id"],
            to_status="in_review",
            disposition="false_positive",
        )


def test_transition_on_missing_alert_raises():
    with pytest.raises(TransitionError, match="not found"):
        dlp_triage.transition(alert_id="does-not-exist", to_status="in_review")


def test_failed_validation_writes_no_event():
    alert = _seed_alert()
    with pytest.raises(TransitionError):
        dlp_triage.transition(alert_id=alert["alert_id"], to_status="closed")
    # Nothing should have been appended to the audit trail.
    assert dlp_triage.list_events(alert["alert_id"]) == []


# ---------------------------------------------------------------------------
# transition — successful state changes
# ---------------------------------------------------------------------------


def test_claim_stamps_assignee_and_claimed_at():
    analyst_id = _seed_analyst()
    alert = _seed_alert()
    updated = dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="in_review",
        actor_id=analyst_id,
        assignee_id=analyst_id,
    )
    assert updated["status"] == "in_review"
    assert updated["assignee_id"] == analyst_id
    assert updated["claimed_at"] is not None
    # Still pending — claim must not touch email scheduling.
    assert updated["email_status"] == "pending"


def test_close_sets_disposition_note_closed_at_and_skips_email():
    alert = _seed_alert()
    dlp_triage.transition(alert_id=alert["alert_id"], to_status="in_review")
    updated = dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="closed",
        disposition="false_positive",
        note="detector misfired",
    )
    assert updated["status"] == "closed"
    assert updated["disposition"] == "false_positive"
    assert updated["disposition_note"] == "detector misfired"
    assert updated["closed_at"] is not None
    # A pending email must be cancelled (→ skipped) when the alert closes.
    assert updated["email_status"] == "skipped"


def test_system_actor_can_close_with_system_disposition():
    alert = _seed_alert()
    updated = dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="closed",
        actor_kind="system",
        disposition="allowlisted",
        note="suppressed by rule",
    )
    assert updated["status"] == "closed"
    assert updated["disposition"] == "allowlisted"


def test_full_lifecycle_new_to_escalated_to_closed():
    alert = _seed_alert()
    dlp_triage.transition(alert_id=alert["alert_id"], to_status="in_review")
    dlp_triage.transition(alert_id=alert["alert_id"], to_status="escalated")
    closed = dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="closed",
        disposition="confirmed_leak",
    )
    assert closed["status"] == "closed"
    assert closed["disposition"] == "confirmed_leak"


def test_closed_alert_is_terminal():
    alert = _seed_alert()
    dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="closed",
        disposition="false_positive",
    )
    with pytest.raises(TransitionError, match="not allowed"):
        dlp_triage.transition(alert_id=alert["alert_id"], to_status="in_review")


# ---------------------------------------------------------------------------
# list_events — audit trail
# ---------------------------------------------------------------------------


def test_events_appended_in_order_with_status_endpoints():
    analyst_id = _seed_analyst()
    alert = _seed_alert()
    dlp_triage.transition(
        alert_id=alert["alert_id"], to_status="in_review", assignee_id=analyst_id
    )
    dlp_triage.transition(alert_id=alert["alert_id"], to_status="escalated")

    events = dlp_triage.list_events(alert["alert_id"])
    assert len(events) == 2
    assert [e["from_status"] for e in events] == ["new", "in_review"]
    assert [e["to_status"] for e in events] == ["in_review", "escalated"]
    assert all(e["event_type"] == "status_change" for e in events)
    # Assignee carries forward on the second event even though it wasn't
    # re-passed (to_assignee falls back to from_assignee).
    assert events[1]["to_assignee"] == analyst_id


def test_list_events_empty_for_untouched_alert():
    alert = _seed_alert()
    assert dlp_triage.list_events(alert["alert_id"]) == []
