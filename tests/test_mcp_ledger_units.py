"""Unit tests for kyde.mcp_ledger's pure helpers and health stamping.

record_mcp_call itself is exercised end-to-end in test_mcp_proxy; here we pin
the branch logic that the proxy tests don't reach — outcome→action mapping,
error-snippet derivation, and the MCP-source DLP alert writer.
"""

from __future__ import annotations

from kyde import ledger, mcp_ledger, mcp_registry
from kyde.dlp import DlpFinding
from kyde.testing import append_simple


# ---------------------------------------------------------------------------
# _action_for / _findings_to_jsonb / now_ms
# ---------------------------------------------------------------------------


def test_action_for_outcome_dominates_method():
    assert mcp_ledger._action_for("tools/call", "blocked") == mcp_ledger.ACTION_BLOCKED
    assert (
        mcp_ledger._action_for("tools/call", "dlp_blocked")
        == mcp_ledger.ACTION_BLOCKED
    )
    assert (
        mcp_ledger._action_for("tools/call", "upstream_error")
        == mcp_ledger.ACTION_UPSTREAM_ERROR
    )
    assert mcp_ledger._action_for("tools/call", "ok") == mcp_ledger.ACTION_TOOL_CALL
    assert (
        mcp_ledger._action_for("resources/read", "ok")
        == mcp_ledger.ACTION_RESOURCES_READ
    )
    assert mcp_ledger._action_for("initialize", "ok") == mcp_ledger.ACTION_GENERIC


def test_findings_to_jsonb_strips_dataclass():
    f = DlpFinding(scanner="regex", alert=True, score=0.9, findings=[{"a": 1}])
    assert mcp_ledger._findings_to_jsonb([f]) == [
        {"scanner": "regex", "alert": True, "score": 0.9, "findings": [{"a": 1}], "error": ""}
    ]


def test_now_ms_is_monotonicish():
    a = mcp_ledger.now_ms()
    b = mcp_ledger.now_ms()
    assert isinstance(a, int) and b >= a


# ---------------------------------------------------------------------------
# _update_server_health
# ---------------------------------------------------------------------------


def _server_row() -> dict:
    return mcp_registry.upsert_server("healthtest", "http://mcp.example:9000")


def _health(server_id: str) -> dict:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_call_at, last_error_at, last_error_status, "
                "last_error_snippet FROM mcp_servers WHERE id = %s",
                (str(server_id),),
            )
            return cur.fetchone()


def test_health_ok_advances_last_call_at():
    row = _server_row()
    mcp_ledger._update_server_health(
        backend=row, outcome="ok", status_code=200, upstream_body={}
    )
    h = _health(row["id"])
    assert h["last_call_at"] is not None
    assert h["last_error_at"] is None


def test_health_upstream_error_writes_snippet_from_body():
    row = _server_row()
    mcp_ledger._update_server_health(
        backend=row,
        outcome="upstream_error",
        status_code=502,
        upstream_body={"error": {"message": "connect timeout"}},
    )
    h = _health(row["id"])
    assert h["last_error_status"] == 502
    assert h["last_error_snippet"] == "connect timeout"


def test_health_error_snippet_falls_back_without_body():
    row = _server_row()
    mcp_ledger._update_server_health(
        backend=row, outcome="upstream_error", status_code=503, upstream_body=None
    )
    assert _health(row["id"])["last_error_snippet"] == "upstream_error (status=503)"


def test_health_5xx_with_ok_outcome_reports_http_status():
    row = _server_row()
    mcp_ledger._update_server_health(
        backend=row, outcome="ok", status_code=500, upstream_body={}
    )
    assert _health(row["id"])["last_error_snippet"] == "HTTP 500"


def test_health_skips_blocks_and_missing_ids():
    row = _server_row()
    # Policy/DLP blocks are caller problems — no health update.
    mcp_ledger._update_server_health(
        backend=row, outcome="dlp_blocked", status_code=403, upstream_body=None
    )
    h = _health(row["id"])
    assert h["last_call_at"] is None and h["last_error_at"] is None
    # Missing id short-circuits before touching the DB.
    mcp_ledger._update_server_health(
        backend={}, outcome="ok", status_code=200, upstream_body=None
    )


# ---------------------------------------------------------------------------
# _write_dlp_alerts
# ---------------------------------------------------------------------------


def _alert_rows() -> list[dict]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scanner, source_type, mcp_server_id, mcp_method, "
                "mcp_tool_name FROM dlp_alerts ORDER BY id"
            )
            return list(cur.fetchall())


def test_write_dlp_alerts_records_mcp_source_columns():
    row = _server_row()
    entry = append_simple("agent:mcp")
    findings = [
        DlpFinding(
            scanner="regex",
            alert=True,
            score=0.9,
            findings=[{"entity_type": "EMAIL_ADDRESS", "text": "a@x.test"}],
        ),
        # No alert → skipped.
        DlpFinding(scanner="bert", alert=False, score=0.1, findings=[]),
        # Alert without findings → also skipped.
        DlpFinding(scanner="bert", alert=True, score=0.9, findings=[]),
    ]
    mcp_ledger._write_dlp_alerts(
        entry=entry,
        backend=row,
        method="tools/call",
        tool_name="search",
        findings=findings,
    )
    rows = _alert_rows()
    assert len(rows) == 1
    assert rows[0]["scanner"] == "regex"
    assert rows[0]["source_type"] == "mcp"
    assert str(rows[0]["mcp_server_id"]) == str(row["id"])
    assert rows[0]["mcp_method"] == "tools/call"
    assert rows[0]["mcp_tool_name"] == "search"
