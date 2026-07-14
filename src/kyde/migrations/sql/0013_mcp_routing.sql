-- MCP routing v1 — per-tenant routing table and per-tool policy.
--
-- mcp_servers is a pure routing table. There are no credential columns:
-- the gateway is transparent on upstream auth (the agent's Authorization
-- header is forwarded unchanged; credential handling is deliberately out
-- of scope). See docs/plans/mcp-routing-v1.md.
--
-- tenant_id is included from day one even though the gateway is
-- effectively single-tenant today, so the hybrid-SaaS rollout (project
-- memory project_saas_direction) doesn't need a backfill migration
-- later. Existing deploys land everything under tenant_id='default'.
--
-- mcp_tool_policies expresses per-(server, agent, tool) allow/deny.
-- '*' wildcards are intentional in both agent_id and tool_name: a single
-- row with ('*','*') is the tenant-wide default for that server. Lookup
-- precedence (most-specific-wins) lives in mcp_proxy.check_policy.

CREATE TABLE IF NOT EXISTS mcp_servers (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    name         TEXT NOT NULL,
    upstream_url TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS mcp_servers_tenant_idx
    ON mcp_servers (tenant_id);

CREATE TABLE IF NOT EXISTS mcp_tool_policies (
    server_id   UUID NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    agent_id    TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    decision    TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (server_id, agent_id, tool_name)
);
