-- M2 — MCP signing + DLP integration.
--
-- The ledger table itself does not change: MCP entries reuse it via
-- action_type='mcp_tool_call'|'mcp_resources_read'|'mcp_blocked'|'mcp_upstream_error'.
-- input_hash and output_hash carry sha256 of the canonical params/result
-- payloads; tool_calls JSONB holds the per-call MCP metadata sidecar
-- (mcp_server_id, mcp_server_name, method, tool_name, outcome, duration_ms,
-- dlp_finding_ids). request_kind mirrors action_type for forward compatibility
-- with existing dashboards. This keeps the signature contract single per the
-- recommendation in docs/plans/mcp-routing-v1.md § Ledger entries.
--
-- dlp_alerts gains four columns so the existing triage flow can surface
-- MCP findings under a Source filter without a parallel table.

ALTER TABLE dlp_alerts
    ADD COLUMN IF NOT EXISTS source_type   TEXT NOT NULL DEFAULT 'chat',
    ADD COLUMN IF NOT EXISTS mcp_server_id UUID,
    ADD COLUMN IF NOT EXISTS mcp_method    TEXT,
    ADD COLUMN IF NOT EXISTS mcp_tool_name TEXT;

CREATE INDEX IF NOT EXISTS dlp_alerts_source_type_idx
    ON dlp_alerts (source_type);
