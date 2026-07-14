-- M5 — Admin action audit log + per-server health surfacing.
--
-- admin_actions captures every mutation through the admin-gated dashboard
-- endpoints (MCP registry CRUD, MCP policy CRUD, DLP policy toggle/resync,
-- ...). before/after snapshot the row as the registry / DB helper returned
-- it so the audit trail is self-contained — no need to join against the
-- live row, which may have been mutated or deleted since.
--
-- actor_username is denormalised: when a user row gets deleted the FK goes
-- NULL but the historical action still shows who did it. This is the
-- standard pattern for forensic logs.
--
-- mcp_servers grows four columns so dashboards can flag a flaky upstream
-- without scanning the ledger. last_call_at advances on every successful
-- call; last_error_* are written when outcome='upstream_error' OR the
-- upstream returned a 5xx. snippet is bounded to 500 chars so a chatty
-- error body cannot bloat the routing table.

CREATE TABLE IF NOT EXISTS admin_actions (
    id             BIGSERIAL PRIMARY KEY,
    actor_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_username TEXT,
    action         TEXT NOT NULL,
    resource_type  TEXT NOT NULL,
    resource_id    TEXT,
    before         JSONB,
    after          JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS admin_actions_actor_idx
    ON admin_actions (actor_id);
CREATE INDEX IF NOT EXISTS admin_actions_ts_idx
    ON admin_actions (created_at DESC);
CREATE INDEX IF NOT EXISTS admin_actions_resource_idx
    ON admin_actions (resource_type, resource_id);

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS last_call_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error_status  INTEGER,
    ADD COLUMN IF NOT EXISTS last_error_snippet TEXT;
