"""Tests for the DLP scanner integration layer (kyde.dlp).

The delta-slicing logic and `render_content_blocks` are covered by
`test_dlp_delta_scan.py` / `test_content_block_rendering.py`. This file
fills the remaining gaps — the parts that actually talk to the two DLP
sidecars and decide what becomes a stored alert:

  * `_check_health` / `health_check` — the status-panel probes, including
    every failure branch (refused / timeout / unexpected).
  * `_scan_bert` / `_scan_regex` — request shaping and the fail-open
    error handling that must turn any HTTP fault into a clean no-alert
    DlpFinding rather than crashing the scan.
  * `scan_text` — concurrency orchestration, the 8000-char truncation,
    the regex-only starter path, and the broad fail-open catch.
  * `_apply_allowlist` — the per-match (regex) and whole-label (bert)
    suppression matrix.
  * `scan_and_store_entry` — the threshold/allowlist/store decision loop,
    exercised against the test DB.
  * `reapply_allowlist_to_open_alerts` + helpers — the retrospective
    sweep that closes or trims already-open alerts.

HTTP is faked with a tiny in-process client (no sockets); the sidecar
helpers all take the client as a parameter, so the fakes are injected
directly rather than monkeypatching httpx globally.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kyde import dlp, ledger
from kyde.dlp import DlpFinding


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for httpx.Response — only what the helpers read."""

    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"http {self.status_code}",
                request=httpx.Request("POST", "http://dlp/x"),
                response=self,  # duck-typed: helpers read .status_code only
            )

    def json(self):
        return self._data


def _client_returning(**methods) -> AsyncMock:
    """Build a fake AsyncClient whose .get/.post resolve to canned
    responses (or raise, when given an Exception via side_effect)."""
    client = AsyncMock()
    for name, value in methods.items():
        if isinstance(value, BaseException):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return client


# ===========================================================================
# bert_enabled / threshold knobs
# ===========================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_bert_enabled_reads_environment(monkeypatch, value, expected):
    monkeypatch.setenv("DLP_BERT_ENABLED", value)
    assert dlp.bert_enabled() is expected


def test_bert_enabled_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("DLP_BERT_ENABLED", raising=False)
    assert dlp.bert_enabled() is True


def test_thresholds_resolve_via_settings(monkeypatch):
    monkeypatch.setattr(dlp.settings, "get", lambda key: "0.42")
    assert dlp.bert_threshold() == 0.42
    assert dlp.regex_threshold() == 0.42


# ===========================================================================
# _check_health / health_check
# ===========================================================================


def test_check_health_ok():
    client = _client_returning(get=FakeResponse({"status": "ok"}))
    out = asyncio.run(dlp._check_health(client, "regex", dlp.DLP_REGEX_URL))
    assert out["name"] == "regex"
    assert out["ok"] is True
    assert out["error"] is None
    assert isinstance(out["latency_ms"], int)


def test_check_health_connection_refused():
    client = _client_returning(get=httpx.ConnectError("refused"))
    out = asyncio.run(dlp._check_health(client, "bert", dlp.DLP_BERT_URL))
    assert out["ok"] is False
    assert out["error"] == "connection refused"
    assert out["latency_ms"] is None


def test_check_health_timeout():
    client = _client_returning(get=httpx.TimeoutException("slow"))
    out = asyncio.run(dlp._check_health(client, "bert", dlp.DLP_BERT_URL))
    assert out["ok"] is False
    assert out["error"] == "timeout"


def test_check_health_unexpected_error_is_stringified():
    client = _client_returning(get=RuntimeError("boom"))
    out = asyncio.run(dlp._check_health(client, "regex", dlp.DLP_REGEX_URL))
    assert out["ok"] is False
    assert "boom" in out["error"]


def test_health_check_aggregates_both_when_bert_enabled(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: True)

    async def fake_probe(client, name, url):
        return {"name": name, "ok": True, "error": None, "latency_ms": 1}

    monkeypatch.setattr(dlp, "_check_health", fake_probe)
    out = asyncio.run(dlp.health_check())
    assert out["ok"] is True
    assert {s["name"] for s in out["scanners"]} == {"bert", "regex"}


def test_health_check_skips_bert_in_starter(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: False)

    async def fake_probe(client, name, url):
        return {"name": name, "ok": True, "error": None, "latency_ms": 1}

    monkeypatch.setattr(dlp, "_check_health", fake_probe)
    out = asyncio.run(dlp.health_check())
    assert [s["name"] for s in out["scanners"]] == ["regex"]


def test_health_check_overall_false_if_any_unhealthy(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: True)

    async def fake_probe(client, name, url):
        ok = name == "regex"
        return {"name": name, "ok": ok, "error": None, "latency_ms": 1}

    monkeypatch.setattr(dlp, "_check_health", fake_probe)
    out = asyncio.run(dlp.health_check())
    assert out["ok"] is False


# ===========================================================================
# _scan_bert
# ===========================================================================


def test_scan_bert_flagged_response():
    resp = FakeResponse(
        {"flagged": True, "label": "PII", "confidence": 0.91, "action": "alert"}
    )
    client = _client_returning(post=resp)
    out = asyncio.run(dlp._scan_bert(client, "ssn 123"))
    assert out.scanner == "bert"
    assert out.alert is True
    assert out.score == 0.91
    assert out.findings[0]["label"] == "PII"
    assert out.error == ""


def test_scan_bert_clean_response():
    resp = FakeResponse({"flagged": False, "confidence": 0.02})
    client = _client_returning(post=resp)
    out = asyncio.run(dlp._scan_bert(client, "hello"))
    assert out.alert is False
    assert out.score == 0.02


def test_scan_bert_connect_error_fails_open():
    client = _client_returning(post=httpx.ConnectError("refused"))
    out = asyncio.run(dlp._scan_bert(client, "x"))
    assert out.alert is False
    assert "connection refused" in out.error


def test_scan_bert_timeout_fails_open():
    client = _client_returning(post=httpx.TimeoutException("slow"))
    out = asyncio.run(dlp._scan_bert(client, "x"))
    assert out.alert is False
    assert "timeout" in out.error


def test_scan_bert_unexpected_error_fails_open():
    client = _client_returning(post=ValueError("weird"))
    out = asyncio.run(dlp._scan_bert(client, "x"))
    assert out.alert is False
    assert "weird" in out.error


# ===========================================================================
# _scan_regex
# ===========================================================================


def test_scan_regex_matches(monkeypatch):
    monkeypatch.setattr(dlp.dlp_policies, "observe_boot_id", MagicMock())
    resp = FakeResponse(
        {
            "total_matches": 2,
            "boot_id": "boot-abc",
            "matches": [
                {"pattern_id": "aws_key", "confidence": 0.8},
                {"pattern_id": "email", "confidence": 0.95},
            ],
        }
    )
    client = _client_returning(post=resp)
    out = asyncio.run(dlp._scan_regex(client, "leak"))
    assert out.alert is True
    assert out.score == 0.95  # max confidence
    assert len(out.findings) == 2
    dlp.dlp_policies.observe_boot_id.assert_called_once_with("boot-abc")


def test_scan_regex_no_matches(monkeypatch):
    monkeypatch.setattr(dlp.dlp_policies, "observe_boot_id", MagicMock())
    resp = FakeResponse({"total_matches": 0, "matches": []})
    client = _client_returning(post=resp)
    out = asyncio.run(dlp._scan_regex(client, "clean"))
    assert out.alert is False
    assert out.score == 0.0


def test_scan_regex_observe_boot_id_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        dlp.dlp_policies,
        "observe_boot_id",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    resp = FakeResponse({"total_matches": 1, "matches": [{"confidence": 0.7}]})
    client = _client_returning(post=resp)
    # Must still return the alert despite the boot-id bookkeeping blowing up.
    out = asyncio.run(dlp._scan_regex(client, "leak"))
    assert out.alert is True
    assert out.score == 0.7


def test_scan_regex_503_triggers_recovery_push(monkeypatch):
    push = MagicMock()
    monkeypatch.setattr(dlp.dlp_policies, "request_recovery_push", push)
    client = _client_returning(post=FakeResponse({}, status_code=503))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False
    assert "not ready" in out.error
    push.assert_called_once()


def test_scan_regex_503_recovery_push_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        dlp.dlp_policies,
        "request_recovery_push",
        MagicMock(side_effect=RuntimeError("nope")),
    )
    client = _client_returning(post=FakeResponse({}, status_code=503))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False  # still fails open


def test_scan_regex_other_http_status(monkeypatch):
    client = _client_returning(post=FakeResponse({}, status_code=500))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False
    assert "http 500" in out.error


def test_scan_regex_connect_error_fails_open():
    client = _client_returning(post=httpx.ConnectError("refused"))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False
    assert "connection refused" in out.error


def test_scan_regex_timeout_fails_open():
    client = _client_returning(post=httpx.TimeoutException("slow"))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False
    assert "timeout" in out.error


def test_scan_regex_unexpected_error_fails_open():
    client = _client_returning(post=KeyError("boom"))
    out = asyncio.run(dlp._scan_regex(client, "x"))
    assert out.alert is False
    assert out.error.startswith("dlp-regex error")


# ===========================================================================
# scan_text
# ===========================================================================


def test_scan_text_empty_returns_empty():
    assert asyncio.run(dlp.scan_text("")) == []
    assert asyncio.run(dlp.scan_text("   ")) == []


def test_scan_text_runs_both_scanners(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: True)
    monkeypatch.setattr(
        dlp, "_scan_bert", AsyncMock(return_value=DlpFinding("bert", False, 0.0))
    )
    monkeypatch.setattr(
        dlp, "_scan_regex", AsyncMock(return_value=DlpFinding("regex", True, 0.9))
    )
    out = asyncio.run(dlp.scan_text("scan me"))
    assert {f.scanner for f in out} == {"bert", "regex"}


def test_scan_text_regex_only_when_bert_disabled(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: False)
    monkeypatch.setattr(
        dlp, "_scan_regex", AsyncMock(return_value=DlpFinding("regex", False, 0.0))
    )
    bert = AsyncMock()
    monkeypatch.setattr(dlp, "_scan_bert", bert)
    out = asyncio.run(dlp.scan_text("scan me"))
    assert [f.scanner for f in out] == ["regex"]
    bert.assert_not_called()


def test_scan_text_truncates_to_8000_chars(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: False)
    captured = {}

    async def fake_regex(client, text, timeout=dlp.DLP_TIMEOUT):
        captured["len"] = len(text)
        return DlpFinding("regex", False, 0.0)

    monkeypatch.setattr(dlp, "_scan_regex", fake_regex)
    asyncio.run(dlp.scan_text("A" * 9000))
    assert captured["len"] == 8000


def test_scan_text_fails_open_on_exception(monkeypatch):
    monkeypatch.setattr(dlp, "bert_enabled", lambda: False)
    monkeypatch.setattr(
        dlp, "_scan_regex", AsyncMock(side_effect=RuntimeError("gather boom"))
    )
    assert asyncio.run(dlp.scan_text("x")) == []


# ===========================================================================
# _apply_allowlist
# ===========================================================================


def _regex_finding(*matches: dict, score: float = 0.9) -> DlpFinding:
    return DlpFinding("regex", True, score, findings=list(matches))


def test_allowlist_passthrough_when_no_alert():
    f = DlpFinding("regex", False, 0.0, findings=[])
    out, suppressed = dlp._apply_allowlist(f)
    assert out is f and suppressed == 0


def test_allowlist_regex_match_with_no_identifiers_is_kept(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", MagicMock())
    f = _regex_finding({"matched_value": "x", "confidence": 0.9})
    out, suppressed = dlp._apply_allowlist(f)
    # No pattern_id/name/entity_type → can't be allowlisted, returned as-is.
    assert out is f and suppressed == 0
    ledger.find_and_bump_allow_rule.assert_not_called()


def test_allowlist_regex_all_suppressed_returns_none(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 7)
    f = _regex_finding({"pattern_id": "aws_key", "confidence": 0.9})
    out, suppressed = dlp._apply_allowlist(f)
    assert out is None
    assert suppressed == 1


def test_allowlist_regex_none_suppressed_returns_original(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: None)
    f = _regex_finding({"pattern_id": "aws_key", "confidence": 0.9})
    out, suppressed = dlp._apply_allowlist(f)
    assert out is f and suppressed == 0


def test_allowlist_regex_partial_recomputes_score(monkeypatch):
    def fake_rule(scanner, candidates, text):
        return 1 if "aws_key" in candidates else None

    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", fake_rule)
    f = _regex_finding(
        {"pattern_id": "aws_key", "confidence": 0.95},
        {"pattern_id": "email", "confidence": 0.40},
        score=0.95,
    )
    out, suppressed = dlp._apply_allowlist(f)
    assert suppressed == 1
    assert len(out.findings) == 1
    assert out.findings[0]["pattern_id"] == "email"
    # Score recomputed from the surviving match, not the original 0.95.
    assert out.score == 0.40


def test_allowlist_bert_label_suppressed(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 3)
    f = DlpFinding("bert", True, 0.9, findings=[{"label": "TOXICITY"}])
    out, suppressed = dlp._apply_allowlist(f)
    assert out is None and suppressed == 1


def test_allowlist_bert_label_not_suppressed(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: None)
    f = DlpFinding("bert", True, 0.9, findings=[{"label": "PII"}])
    out, suppressed = dlp._apply_allowlist(f)
    assert out is f and suppressed == 0


def test_allowlist_bert_empty_label_passes_through(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", MagicMock())
    f = DlpFinding("bert", True, 0.9, findings=[{"label": ""}])
    out, suppressed = dlp._apply_allowlist(f)
    assert out is f and suppressed == 0
    ledger.find_and_bump_allow_rule.assert_not_called()


def test_allowlist_unknown_scanner_passthrough():
    f = DlpFinding("mystery", True, 0.5, findings=[{"x": 1}])
    out, suppressed = dlp._apply_allowlist(f)
    assert out is f and suppressed == 0


# ===========================================================================
# scan_and_store_entry — threshold / allowlist / store decision loop
# ===========================================================================


def _count_alerts() -> int:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM dlp_alerts")
            return cur.fetchone()["n"]


def _only_alert() -> dict:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM dlp_alerts")
            rows = list(cur.fetchall())
    assert len(rows) == 1
    return rows[0]


def _store(findings: list[DlpFinding], *, bert=0.5, regex=0.5):
    """Run scan_and_store_entry with scan_text stubbed to return `findings`."""
    msgs = [{"role": "user", "content": "secret payload here"}]

    async def _go():
        with (
            patch.object(dlp, "scan_text", AsyncMock(return_value=findings)),
            patch.object(dlp, "bert_threshold", lambda: bert),
            patch.object(dlp, "regex_threshold", lambda: regex),
        ):
            await dlp.scan_and_store_entry(
                entry_id="entry-store-1",
                session_id="sess-store-1",
                seq=1,
                messages=msgs,
                response_body={"choices": [{"message": {"content": "ok"}}]},
            )

    asyncio.run(_go())


def test_store_skips_finding_with_error():
    _store(
        [DlpFinding("regex", True, 0.99, findings=[{"pattern_id": "x"}], error="boom")]
    )
    assert _count_alerts() == 0


def test_store_skips_clean_finding():
    _store([DlpFinding("regex", False, 0.0, findings=[])])
    assert _count_alerts() == 0


def test_store_suppresses_below_threshold():
    _store(
        [
            DlpFinding(
                "regex", True, 0.30, findings=[{"pattern_id": "x", "confidence": 0.30}]
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 0


def test_store_inserts_above_threshold():
    _store(
        [
            DlpFinding(
                "regex",
                True,
                0.80,
                findings=[{"pattern_id": "aws_key", "confidence": 0.80}],
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 1
    row = _only_alert()
    assert row["scanner"] == "regex"
    assert row["status"] == "new"


def test_store_fully_allowlisted_is_not_stored(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 1)
    _store(
        [
            DlpFinding(
                "regex",
                True,
                0.80,
                findings=[{"pattern_id": "aws_key", "confidence": 0.80}],
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 0


def test_store_partially_allowlisted_keeps_survivors(monkeypatch):
    def fake_rule(scanner, candidates, text):
        return 1 if "aws_key" in candidates else None

    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", fake_rule)
    _store(
        [
            DlpFinding(
                "regex",
                True,
                0.80,
                findings=[
                    {"pattern_id": "aws_key", "confidence": 0.80},
                    {"pattern_id": "email", "confidence": 0.60},
                ],
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 1
    row = _only_alert()
    kept = [m["pattern_id"] for m in row["findings"]]
    assert kept == ["email"]


def test_store_allowlist_lookup_failure_falls_through(monkeypatch):
    # A rule-lookup error must not drop the alert — it stores unfiltered.
    monkeypatch.setattr(
        ledger,
        "find_and_bump_allow_rule",
        MagicMock(side_effect=RuntimeError("rule table gone")),
    )
    _store(
        [
            DlpFinding(
                "regex",
                True,
                0.80,
                findings=[{"pattern_id": "aws_key", "confidence": 0.80}],
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 1


def test_store_empty_text_skips_scan():
    # Response + delta both empty → returns before calling scan_text.
    scan = AsyncMock(return_value=[])

    async def _go():
        with patch.object(dlp, "scan_text", scan):
            await dlp.scan_and_store_entry(
                entry_id="e",
                session_id="s",
                seq=1,
                messages=[{"role": "user", "content": ""}],
                response_body={},
            )

    asyncio.run(_go())
    scan.assert_not_called()


def test_store_handles_no_findings_returned():
    _store([])  # scan_text returned [] → nothing stored, no crash
    assert _count_alerts() == 0


def test_store_dedup_hit_does_not_create_second_alert():
    finding = DlpFinding(
        "regex", True, 0.80, findings=[{"pattern_id": "aws_key", "confidence": 0.80}]
    )
    _store([finding], regex=0.50)
    _store([finding], regex=0.50)  # identical → dedup, bumps seen_count
    assert _count_alerts() == 1
    row = _only_alert()
    assert row["seen_count"] == 2


def test_store_never_raises_even_if_delta_lookup_fails(monkeypatch):
    # The outer catch is the fire-and-forget safety net: a failure before
    # the scan (here, the prior-length lookup) must be swallowed, never
    # propagated into the event loop.
    monkeypatch.setattr(
        ledger,
        "get_prior_full_messages_length",
        MagicMock(side_effect=RuntimeError("db unreachable")),
    )

    async def _go():
        await dlp.scan_and_store_entry(
            entry_id="e",
            session_id="s",
            seq=1,
            messages=[{"role": "user", "content": "hi"}],
            response_body={},
        )

    asyncio.run(_go())  # must not raise
    assert _count_alerts() == 0


def test_store_upsert_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "upsert_dlp_alert",
        MagicMock(side_effect=RuntimeError("insert exploded")),
    )
    # Must not raise out of the fire-and-forget task.
    _store(
        [
            DlpFinding(
                "regex", True, 0.80, findings=[{"pattern_id": "x", "confidence": 0.80}]
            )
        ],
        regex=0.50,
    )
    assert _count_alerts() == 0


def test_store_response_extraction_tolerates_empty_choices():
    # An empty `choices` list makes the [0] index raise IndexError, which
    # the response-extraction guard swallows — the request-side text is
    # still scanned and the finding stored.
    findings = [
        DlpFinding(
            "regex", True, 0.80, findings=[{"pattern_id": "x", "confidence": 0.80}]
        )
    ]

    async def _go():
        with (
            patch.object(dlp, "scan_text", AsyncMock(return_value=findings)),
            patch.object(dlp, "regex_threshold", lambda: 0.5),
            patch.object(dlp, "bert_threshold", lambda: 0.5),
        ):
            await dlp.scan_and_store_entry(
                entry_id="entry-badresp",
                session_id="sess-badresp",
                seq=1,
                messages=[{"role": "user", "content": "hi"}],
                response_body={"choices": []},
            )

    asyncio.run(_go())
    assert _count_alerts() == 1


# ===========================================================================
# reapply_allowlist_to_open_alerts + helpers
# ===========================================================================


def _seed_open_alert(scanner: str, findings: list[dict], entry="e1", sess="s1") -> dict:
    row, is_new = ledger.upsert_dlp_alert(entry, sess, scanner, 0.9, findings)
    assert is_new
    return row


def test_reapply_regex_fully_allowlisted_closes_alert(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 1)
    alert = _seed_open_alert("regex", [{"pattern_id": "aws_key", "confidence": 0.9}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result == {
        "scanned": 1,
        "fully_allowlisted": 1,
        "partially_updated": 0,
        "unchanged": 0,
    }
    closed = ledger.get_dlp_alert(alert["alert_id"])
    assert closed["status"] == "closed"
    assert closed["disposition"] == "allowlisted"


def test_reapply_regex_partial_updates_in_place(monkeypatch):
    def fake_rule(scanner, candidates, text):
        return 1 if "aws_key" in candidates else None

    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", fake_rule)
    alert = _seed_open_alert(
        "regex",
        [
            {"pattern_id": "aws_key", "confidence": 0.95},
            {"pattern_id": "email", "confidence": 0.55},
        ],
    )
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["partially_updated"] == 1
    updated = ledger.get_dlp_alert(alert["alert_id"])
    assert updated["status"] != "closed"
    kept = [m["pattern_id"] for m in updated["findings"]]
    assert kept == ["email"]
    # Score recomputed to the surviving match's confidence.
    assert float(updated["score"]) == 0.55


def test_reapply_regex_unchanged_when_no_rule_matches(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: None)
    _seed_open_alert("regex", [{"pattern_id": "aws_key", "confidence": 0.9}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["unchanged"] == 1
    assert result["fully_allowlisted"] == 0


def test_reapply_bert_label_allowlisted_closes(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 2)
    alert = _seed_open_alert("bert", [{"label": "TOXICITY", "confidence": 0.9}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["fully_allowlisted"] == 1
    assert ledger.get_dlp_alert(alert["alert_id"])["status"] == "closed"


def test_reapply_bert_unchanged_when_label_not_allowlisted(monkeypatch):
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: None)
    _seed_open_alert("bert", [{"label": "PII", "confidence": 0.9}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["unchanged"] == 1


def test_reapply_regex_match_without_identifiers_is_kept(monkeypatch):
    # A finding carrying no pattern_id/name/entity_type can't be matched
    # against a rule, so it's kept and the alert is left unchanged.
    rule = MagicMock()
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", rule)
    _seed_open_alert("regex", [{"matched_value": "x", "confidence": 0.9}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["unchanged"] == 1
    rule.assert_not_called()


def test_reapply_unknown_scanner_is_unchanged():
    _seed_open_alert("mystery", [{"foo": "bar"}])
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result == {
        "scanned": 1,
        "fully_allowlisted": 0,
        "partially_updated": 0,
        "unchanged": 1,
    }


def test_reapply_skips_closed_alerts(monkeypatch):
    # A closed alert must not be swept again.
    monkeypatch.setattr(ledger, "find_and_bump_allow_rule", lambda *a: 1)
    alert = _seed_open_alert("regex", [{"pattern_id": "aws_key", "confidence": 0.9}])
    from kyde import dlp_triage

    dlp_triage.transition(
        alert_id=alert["alert_id"],
        to_status="closed",
        disposition="false_positive",
    )
    result = dlp.reapply_allowlist_to_open_alerts()
    assert result["scanned"] == 0


def test_mark_alert_allowlisted_routes_through_triage():
    alert = _seed_open_alert("regex", [{"pattern_id": "x", "confidence": 0.9}])
    from kyde import dlp_triage

    dlp._mark_alert_allowlisted(alert["alert_id"])
    closed = ledger.get_dlp_alert(alert["alert_id"])
    assert closed["status"] == "closed"
    assert closed["disposition"] == "allowlisted"
    # The close shows up in the triage audit trail as a system event.
    events = dlp_triage.list_events(alert["alert_id"])
    assert events[-1]["to_status"] == "closed"


def test_update_alert_partial_rewrites_findings_and_score():
    alert = _seed_open_alert(
        "regex",
        [
            {"pattern_id": "aws_key", "confidence": 0.95},
            {"pattern_id": "email", "confidence": 0.30},
        ],
    )
    dlp._update_alert_partial(
        alert["id"], [{"pattern_id": "email", "confidence": 0.30}]
    )
    updated = ledger.get_dlp_alert(alert["alert_id"])
    assert [m["pattern_id"] for m in updated["findings"]] == ["email"]
    assert float(updated["score"]) == 0.30
