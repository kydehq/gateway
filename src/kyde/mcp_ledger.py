"""Per-call MCP ledger writer.

Each MCP JSON-RPC request (resolved, forwarded, or blocked) lands as a
signed row in the existing `ledger` table. We reuse the chat-side ledger
schema via the `action_type` discriminator and stuff per-call MCP
metadata into the existing `tool_calls` JSONB sidecar — so MCP rows share
the single signing / verification / export path instead of growing a
parallel table.

For DLP findings produced by `dlp_json_walk`, we additionally write a
row to `dlp_alerts` with `source_type='mcp'` so the existing triage
flow surfaces them under a Source filter without a parallel table.
"""

from __future__ import annotations

import time
from typing import Literal, Optional

import httpx
from fastapi import Request

from . import dlp, ledger


# action_type discriminator values for MCP rows. request_kind mirrors
# the action_type so existing dashboards that filter by request_kind
# can light up MCP without code changes.
ACTION_TOOL_CALL = "mcp_tool_call"
ACTION_RESOURCES_READ = "mcp_resources_read"
ACTION_BLOCKED = "mcp_blocked"
ACTION_UPSTREAM_ERROR = "mcp_upstream_error"
ACTION_GENERIC = "mcp_call"


Outcome = Literal["ok", "blocked", "upstream_error", "dlp_blocked"]


def _action_for(method: str, outcome: Outcome) -> str:
    """Pick the action_type / request_kind for this entry.

    Outcome dominates method — a blocked tools/call must surface as
    `mcp_blocked` in dashboards so operators can find policy denials
    without re-parsing tool_calls metadata. Same for upstream errors.
    """
    if outcome == "blocked" or outcome == "dlp_blocked":
        return ACTION_BLOCKED
    if outcome == "upstream_error":
        return ACTION_UPSTREAM_ERROR
    if method == "tools/call":
        return ACTION_TOOL_CALL
    if method == "resources/read":
        return ACTION_RESOURCES_READ
    return ACTION_GENERIC


def _findings_to_jsonb(findings: list[dlp.DlpFinding]) -> list[dict]:
    """Strip the dataclass wrapper so JSONB serialisation works cleanly."""
    return [
        {
            "scanner": f.scanner,
            "alert": f.alert,
            "score": f.score,
            "findings": f.findings,
            "error": f.error,
        }
        for f in findings
    ]


async def record_mcp_call(
    *,
    request: Request,
    backend: dict,
    envelope: dict,
    upstream_response: Optional[httpx.Response],
    upstream_body: Optional[dict],
    outcome: Outcome,
    duration_ms: int,
    dlp_findings: list[dlp.DlpFinding],
) -> ledger.LedgerEntry:
    """Build, sign, and persist the ledger row for one MCP call.

    `upstream_body` is the parsed JSON-RPC response (or None when the call
    was blocked or transport-failed). It's hashed into output_hash; the
    raw upstream_response is only consulted for status_code metadata.

    Per-tenant agent identification reuses the chat-side `_agent_id`
    helper. Imported locally to avoid a module-load-time circular
    dependency between server.py (which imports mcp_proxy) and this file
    (which is imported by mcp_proxy).
    """
    from .server import _agent_id, _client_ip  # local import: see docstring

    method = str(envelope.get("method") or "")
    params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}
    tool_name = ""
    if method == "tools/call" and isinstance(params, dict):
        tool_name = str(params.get("name") or "")

    action_type = _action_for(method, outcome)
    request_kind = action_type  # interpretation mirror; excluded from signature.

    status_code = upstream_response.status_code if upstream_response is not None else 0

    sidecar = [
        {
            "mcp_server_id": str(backend.get("id") or ""),
            "mcp_server_name": str(backend.get("name") or ""),
            "method": method,
            "tool_name": tool_name,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "status_code": status_code,
            "dlp_finding_count": sum(len(f.findings) for f in dlp_findings if f.alert),
        }
    ]

    request_body = params if isinstance(params, dict) else {}
    response_body = upstream_body if isinstance(upstream_body, dict) else {}

    user_agent = request.headers.get("user-agent", "")
    client_ip = _client_ip(request)
    agent_id = _agent_id(request)

    entry = ledger.append(
        agent_id=agent_id,
        action_type=action_type,
        model=f"mcp:{backend.get('name', '')}",
        request_body=request_body,
        response_body=response_body,
        why_messages=[],
        tool_calls=sidecar,
        client_ip=client_ip,
        user_agent=user_agent,
        session_id="",
        upstream=str(backend.get("upstream_url") or ""),
        full_messages=[],
        prompt_tokens=0,
        completion_tokens=0,
        request_kind=request_kind,
    )

    if dlp_findings:
        _write_dlp_alerts(
            entry=entry,
            backend=backend,
            method=method,
            tool_name=tool_name,
            findings=dlp_findings,
        )

    _update_server_health(
        backend=backend,
        outcome=outcome,
        status_code=status_code,
        upstream_body=upstream_body,
    )
    return entry


def _update_server_health(
    *,
    backend: dict,
    outcome: Outcome,
    status_code: int,
    upstream_body: Optional[dict],
) -> None:
    """Stamp mcp_servers with per-call health so dashboards can flag a
    flaky upstream without scanning the ledger.

    - outcome=='ok' advances last_call_at.
    - outcome=='upstream_error' OR status_code>=500 writes last_error_*.
    - 4xx and policy/DLP blocks don't update health — those are caller
      problems, not upstream problems.

    Snippet is bounded to 500 chars so a chatty error body cannot bloat
    the routing table. mcp_registry's cache is invalidated so the next
    GET /api/mcp/servers reflects the new fields.
    """
    server_id = backend.get("id")
    if not server_id:
        return
    is_error = outcome == "upstream_error" or status_code >= 500
    is_ok = outcome == "ok"
    if not (is_ok or is_error):
        return

    snippet: Optional[str] = None
    if is_error:
        if isinstance(upstream_body, dict):
            err = upstream_body.get("error")
            if isinstance(err, dict):
                snippet = str(err.get("message") or "")[:500]
        if not snippet:
            snippet = (
                f"upstream_error (status={status_code})"
                if outcome == "upstream_error"
                else f"HTTP {status_code}"
            )

    try:
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                if is_error:
                    cur.execute(
                        """
                        UPDATE mcp_servers
                           SET last_error_at = now(),
                               last_error_status = %s,
                               last_error_snippet = %s
                         WHERE id = %s
                        """,
                        (status_code or None, snippet, str(server_id)),
                    )
                else:
                    cur.execute(
                        "UPDATE mcp_servers SET last_call_at = now() WHERE id = %s",
                        (str(server_id),),
                    )
            conn.commit()
    except Exception:
        # Health-stamping is non-critical telemetry. A schema mismatch
        # (older deploy without migration 0016) or a transient DB hiccup
        # must not fail the surrounding ledger write.
        return
    from . import mcp_registry

    mcp_registry.invalidate_cache()


def _write_dlp_alerts(
    *,
    entry: ledger.LedgerEntry,
    backend: dict,
    method: str,
    tool_name: str,
    findings: list[dlp.DlpFinding],
) -> None:
    """One dlp_alerts row per scanner that flagged. Mirrors what the chat
    path does in `dlp.scan_and_store_entry` — same dedup/dedup-hash logic
    via `upsert_dlp_alert`, just with the MCP source columns set so the
    triage UI can split them out."""
    server_id = str(backend.get("id") or "") or None
    for finding in findings:
        if not finding.alert or not finding.findings:
            continue
        ledger.upsert_dlp_alert(
            entry.entry_id,
            "",  # no session_id for MCP rows (yet)
            finding.scanner,
            float(finding.score or 0.0),
            finding.findings,
            source_type="mcp",
            mcp_server_id=server_id,
            mcp_method=method or None,
            mcp_tool_name=tool_name or None,
        )


def now_ms() -> int:
    """Monotonic-ish millisecond stamp for duration_ms."""
    return int(time.perf_counter() * 1000)
