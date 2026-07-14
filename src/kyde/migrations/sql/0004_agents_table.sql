-- Materialized `agents` table — gives every agent_id a canonical row that
-- admins can attach a human-readable `display_name` to. Without this, the
-- frontend can only auto-derive labels like "Claude Code Agent (abc12345)"
-- from the agent's hash, which is unhelpful when an org runs many agents
-- of the same tool. Backfilled from existing ledger.agent_id values and
-- kept in sync by an AFTER INSERT trigger.
--
-- `display_name` is nullable: an unnamed row signals "use the hash-derived
-- default". The dashboard PATCH endpoint sets it.

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,
    display_name  TEXT,
    first_seen    DOUBLE PRECISION NOT NULL,
    last_seen     DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS agents_last_seen_idx ON agents (last_seen DESC);

INSERT INTO agents (agent_id, first_seen, last_seen)
SELECT agent_id, MIN(timestamp), MAX(timestamp)
  FROM ledger
 WHERE agent_id <> ''
 GROUP BY agent_id
 ORDER BY MIN(timestamp) ASC
    ON CONFLICT (agent_id) DO NOTHING;

CREATE OR REPLACE FUNCTION agents_upsert_from_ledger() RETURNS trigger AS $$
BEGIN
    IF NEW.agent_id = '' THEN
        RETURN NEW;
    END IF;
    INSERT INTO agents (agent_id, first_seen, last_seen)
    VALUES (NEW.agent_id, NEW.timestamp, NEW.timestamp)
    ON CONFLICT (agent_id) DO UPDATE
       SET last_seen = GREATEST(agents.last_seen, EXCLUDED.last_seen);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agents_upsert_trigger ON ledger;
CREATE TRIGGER agents_upsert_trigger
    AFTER INSERT ON ledger
    FOR EACH ROW EXECUTE FUNCTION agents_upsert_from_ledger();
