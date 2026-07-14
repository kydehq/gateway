"""Tests for the alert-severity rollup in kyde.ledger.

Severity used to be a constant column default ('medium') because
upsert_dlp_alert never wrote it. The fix derives it from the per-finding
severity (set by the YAML rule that fired) and never demotes a parent
alert on dedup-merge.
"""

from __future__ import annotations

from kyde import ledger
from kyde.ledger import _rollup_severity


# ---------------------------------------------------------------------------
# Unit — pure rollup function
# ---------------------------------------------------------------------------


def test_rollup_picks_max_severity():
    findings = [{"severity": "MEDIUM"}, {"severity": "CRITICAL"}, {"severity": "LOW"}]
    assert _rollup_severity(findings) == "CRITICAL"


def test_rollup_is_case_insensitive():
    assert _rollup_severity([{"severity": "low"}]) == "LOW"
    assert _rollup_severity([{"severity": "High"}]) == "HIGH"


def test_rollup_defaults_to_medium_when_empty_or_unknown():
    assert _rollup_severity([]) == "MEDIUM"
    assert _rollup_severity([{}]) == "MEDIUM"
    assert _rollup_severity([{"severity": "weird-value"}]) == "MEDIUM"


# ---------------------------------------------------------------------------
# Integration — upsert_dlp_alert against the test DB
# ---------------------------------------------------------------------------


def _finding(pid: str, severity: str, value: str = "x") -> dict:
    return {
        "pattern_id": pid,
        "entity_type": pid,
        "matched_value": value,
        "severity": severity,
    }


def test_insert_writes_severity_from_findings():
    row, is_new = ledger.upsert_dlp_alert(
        "entry-1",
        "session-1",
        "regex",
        0.95,
        [_finding("aws_key", "CRITICAL", "AKIA...")],
    )
    assert is_new
    assert row["severity"] == "CRITICAL"


def test_dedup_merge_promotes_severity_when_worse_finding_arrives():
    # First detection: MEDIUM
    first, _ = ledger.upsert_dlp_alert(
        "entry-1",
        "session-1",
        "regex",
        0.7,
        [_finding("email", "MEDIUM", "a@b.com")],
    )
    assert first["severity"] == "MEDIUM"

    # Same dedup key (same pattern + same text) but caller now declares
    # the finding's severity as CRITICAL. Parent alert must be promoted.
    second, is_new = ledger.upsert_dlp_alert(
        "entry-2",
        "session-1",
        "regex",
        0.7,
        [_finding("email", "CRITICAL", "a@b.com")],
    )
    assert not is_new
    assert second["severity"] == "CRITICAL"
    assert second["id"] == first["id"]


def test_dedup_merge_does_not_demote_severity():
    first, _ = ledger.upsert_dlp_alert(
        "entry-1",
        "session-1",
        "regex",
        0.9,
        [_finding("token", "HIGH", "tok-1")],
    )
    assert first["severity"] == "HIGH"

    second, is_new = ledger.upsert_dlp_alert(
        "entry-2",
        "session-1",
        "regex",
        0.5,
        [_finding("token", "LOW", "tok-1")],
    )
    assert not is_new
    assert second["severity"] == "HIGH"  # stays at HIGH, not demoted to LOW
