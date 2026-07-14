-- Admin-supplied block list. Agents matching a row here are rejected by
-- the proxy with HTTP 403 and the rejection is logged to the ledger as
-- action_type='policy_block' so the audit trail records the prevention.

CREATE TABLE IF NOT EXISTS agent_blocks (
    agent_id    TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    blocked_at  DOUBLE PRECISION NOT NULL,
    blocked_by  BIGINT,
    reason      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS agent_blocks_blocked_at_idx
    ON agent_blocks (blocked_at DESC);
