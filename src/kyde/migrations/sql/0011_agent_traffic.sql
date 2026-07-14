-- Per-agent traffic metering + per-(agent, path_kind) full-logging mode.
--
-- The proxy filters non-chat endpoints (embeddings, models-list,
-- moderations, audio, image, ...) out of full ledger logging via
-- _should_log_path. That keeps the ledger lean but leaves operators
-- blind to what unsupported traffic each agent is actually sending.
--
-- agent_traffic_meters is the always-on counter — every request the
-- proxy sees (chat, non-chat, blocked) increments it. agent_traffic_mode_history
-- is the operator-controlled switch — by default a (agent, path_kind) tuple
-- is 'count_only' (just the counter); flipping it to 'full_logging' causes
-- the proxy to also write a full ledger row from then on. The history table
-- is append-only so the audit trail of mode flips survives across multiple
-- enable/disable cycles.
--
-- Phase B1 (this migration) ships the tables, the counter, and the mode
-- CRUD. Phase B2 wires mode → ledger-write behavior in the proxy.

CREATE TABLE IF NOT EXISTS agent_traffic_meters (
    agent_id    TEXT NOT NULL,
    path_kind   TEXT NOT NULL,
    count       BIGINT NOT NULL DEFAULT 0,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, path_kind)
);

CREATE INDEX IF NOT EXISTS agent_traffic_meters_last_seen_idx
    ON agent_traffic_meters (last_seen DESC);

CREATE INDEX IF NOT EXISTS agent_traffic_meters_agent_idx
    ON agent_traffic_meters (agent_id);

CREATE TABLE IF NOT EXISTS agent_traffic_mode_history (
    id          BIGSERIAL PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    path_kind   TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('count_only', 'full_logging')),
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- Most queries read "what's the latest mode for (agent, path_kind)?" — an
-- index on (agent_id, path_kind, changed_at DESC) keeps that to a single
-- index seek even as history accumulates.
CREATE INDEX IF NOT EXISTS agent_traffic_mode_history_lookup_idx
    ON agent_traffic_mode_history (agent_id, path_kind, changed_at DESC);
