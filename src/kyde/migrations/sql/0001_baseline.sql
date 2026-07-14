-- Baseline schema: extracted verbatim from ledger.py:_SCHEMA_SQL at the
-- point the migration runner was introduced. Everything here is idempotent
-- (IF NOT EXISTS / IF NOT EXISTS-equivalent DO blocks) so re-application
-- against legacy databases that already have these objects is safe.
--
-- Subsequent changes belong in NNNN_*.sql files, not edits to this one.

-- pg_trgm powers the dashboard's substring search. Shipped with every stock
-- Postgres build since 9.1; CREATE IF NOT EXISTS is idempotent.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS ledger (
    seq               BIGSERIAL PRIMARY KEY,
    entry_id          TEXT UNIQUE NOT NULL,
    timestamp         DOUBLE PRECISION NOT NULL,
    agent_id          TEXT NOT NULL,
    action_type       TEXT NOT NULL,
    model             TEXT NOT NULL,
    why               JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_hash        TEXT NOT NULL,
    output_hash       TEXT NOT NULL,
    tool_calls        JSONB NOT NULL DEFAULT '[]'::jsonb,
    prev_hash         TEXT NOT NULL,
    entry_hash        TEXT NOT NULL,
    signature         TEXT NOT NULL,
    client_ip         TEXT NOT NULL DEFAULT '',
    user_agent        TEXT NOT NULL DEFAULT '',
    session_id        TEXT NOT NULL DEFAULT '',
    upstream          TEXT NOT NULL DEFAULT '',
    full_messages     JSONB NOT NULL DEFAULT '[]'::jsonb,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ledger_ts_idx       ON ledger (timestamp DESC);
CREATE INDEX IF NOT EXISTS ledger_agent_idx    ON ledger (agent_id);
CREATE INDEX IF NOT EXISTS ledger_session_idx  ON ledger (session_id);
CREATE INDEX IF NOT EXISTS ledger_action_idx   ON ledger (action_type);
CREATE INDEX IF NOT EXISTS ledger_upstream_idx ON ledger (upstream);
CREATE INDEX IF NOT EXISTS ledger_search_idx   ON ledger
    USING gin ((coalesce(agent_id,'') || ' ' || coalesce(model,'') || ' ' || coalesce(entry_id,'') || ' ' || coalesce(client_ip,'') || ' ' || coalesce(session_id,'')) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS dlp_alerts (
    id                 BIGSERIAL PRIMARY KEY,
    alert_id           TEXT UNIQUE NOT NULL,
    entry_id           TEXT NOT NULL,
    session_id         TEXT NOT NULL DEFAULT '',
    scanner            TEXT NOT NULL,
    score              DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    findings           JSONB NOT NULL DEFAULT '[]'::jsonb,
    status             TEXT NOT NULL DEFAULT 'new',
    created_at         DOUBLE PRECISION NOT NULL,
    updated_at         DOUBLE PRECISION NOT NULL,
    dedup_hash         TEXT NOT NULL DEFAULT '',
    last_seen_entry_id TEXT NOT NULL DEFAULT '',
    last_seen_at       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    seen_count         INTEGER NOT NULL DEFAULT 1,
    email_status       TEXT NOT NULL DEFAULT 'none',
    email_sent_at      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    email_attempts     INTEGER NOT NULL DEFAULT 0,
    email_last_error   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS dlp_alerts_status_idx ON dlp_alerts (status, created_at DESC);

ALTER TABLE dlp_alerts
    ADD COLUMN IF NOT EXISTS dedup_hash         TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_seen_entry_id TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_seen_at       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS seen_count         INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS email_status       TEXT   NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS email_sent_at      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS email_attempts     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS email_last_error   TEXT   NOT NULL DEFAULT '';

UPDATE dlp_alerts
   SET dedup_hash         = alert_id,
       last_seen_entry_id = entry_id,
       last_seen_at       = created_at
 WHERE dedup_hash = '';

CREATE UNIQUE INDEX IF NOT EXISTS dlp_alerts_dedup_open_idx
    ON dlp_alerts (dedup_hash)
 WHERE status IN ('new', 'analysis_in_progress');

CREATE INDEX IF NOT EXISTS dlp_alerts_email_pending_idx
    ON dlp_alerts (id)
 WHERE email_status = 'pending';

CREATE TABLE IF NOT EXISTS users (
    id                   BIGSERIAL PRIMARY KEY,
    username             TEXT UNIQUE NOT NULL,
    email                TEXT NOT NULL DEFAULT '',
    password_hash        TEXT NOT NULL,
    roles                JSONB NOT NULL DEFAULT '["viewer"]'::jsonb,
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_count   INTEGER NOT NULL DEFAULT 0,
    locked_at            DOUBLE PRECISION,
    created_at           DOUBLE PRECISION NOT NULL,
    last_login_at        DOUBLE PRECISION,
    password_changed_at  DOUBLE PRECISION NOT NULL,
    deleted_at           DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS users_roles_idx ON users USING gin (roles jsonb_path_ops);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL,
    updated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dlp_rules (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,
    scanner      TEXT,
    entity_type  TEXT NOT NULL,
    match_text   TEXT,
    note         TEXT NOT NULL DEFAULT '',
    hit_count    INTEGER NOT NULL DEFAULT 0,
    last_hit_at  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_by   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at   DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS dlp_rules_unique_idx
    ON dlp_rules (kind, COALESCE(scanner, ''), entity_type, COALESCE(match_text, ''));
CREATE INDEX IF NOT EXISTS dlp_rules_lookup_idx
    ON dlp_rules (kind, entity_type);

UPDATE dlp_rules
   SET entity_type = LOWER(entity_type)
 WHERE entity_type <> LOWER(entity_type);
UPDATE dlp_rules
   SET scanner = LOWER(scanner)
 WHERE scanner IS NOT NULL
   AND scanner <> LOWER(scanner);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id  TEXT NOT NULL,
    turn_hash   TEXT NOT NULL,
    first_seen  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (session_id, turn_hash)
);
CREATE INDEX IF NOT EXISTS session_turns_hash_idx
    ON session_turns (turn_hash, first_seen DESC);

ALTER TABLE dlp_alerts
    ADD COLUMN IF NOT EXISTS assignee_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS severity         TEXT   NOT NULL DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS disposition      TEXT,
    ADD COLUMN IF NOT EXISTS disposition_note TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS claimed_at       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS closed_at        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS reopened_at      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS reopen_count     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS linked_incident  TEXT   NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS tags             JSONB  NOT NULL DEFAULT '[]'::jsonb;

UPDATE dlp_alerts
   SET status      = 'closed',
       disposition = 'allowlisted',
       closed_at   = COALESCE(closed_at, updated_at)
 WHERE status = 'allowlisted';
UPDATE dlp_alerts
   SET status      = 'closed',
       disposition = 'false_positive',
       closed_at   = COALESCE(closed_at, updated_at)
 WHERE status = 'false_positive';
UPDATE dlp_alerts
   SET status      = 'closed',
       disposition = 'confirmed_leak',
       closed_at   = COALESCE(closed_at, updated_at)
 WHERE status = 'data_leak';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'dlp_alerts_disposition_ck'
    ) THEN
        ALTER TABLE dlp_alerts
            ADD CONSTRAINT dlp_alerts_disposition_ck
            CHECK (
                (status =  'closed' AND disposition IS NOT NULL) OR
                (status <> 'closed' AND disposition IS NULL)
            );
    END IF;
END $$;

DROP INDEX IF EXISTS dlp_alerts_dedup_open_idx;
CREATE UNIQUE INDEX IF NOT EXISTS dlp_alerts_dedup_open_idx
    ON dlp_alerts (dedup_hash)
 WHERE status <> 'closed';

CREATE INDEX IF NOT EXISTS dlp_alerts_assignee_idx
    ON dlp_alerts (assignee_id, status) WHERE status <> 'closed';

CREATE TABLE IF NOT EXISTS dlp_alert_events (
    id            BIGSERIAL PRIMARY KEY,
    alert_id      TEXT NOT NULL REFERENCES dlp_alerts(alert_id) ON DELETE CASCADE,
    actor_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_kind    TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    from_status   TEXT,
    to_status     TEXT,
    from_assignee BIGINT,
    to_assignee   BIGINT,
    disposition   TEXT,
    note          TEXT NOT NULL DEFAULT '',
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS dlp_alert_events_alert_idx
    ON dlp_alert_events (alert_id, created_at);

CREATE TABLE IF NOT EXISTS request_network (
    seq               BIGINT PRIMARY KEY
                       REFERENCES ledger(seq) ON DELETE CASCADE,
    timestamp         DOUBLE PRECISION NOT NULL,
    remote_addr       INET,
    forwarded_chain   JSONB NOT NULL DEFAULT '[]'::jsonb,
    forwarded_for_raw TEXT NOT NULL DEFAULT '',
    forwarded_raw     TEXT NOT NULL DEFAULT '',
    via_raw           TEXT NOT NULL DEFAULT '',
    origin_ip         INET,
    origin_class      TEXT NOT NULL DEFAULT 'unknown',
    origin_subnet     TEXT NOT NULL DEFAULT '',
    ua_tool           TEXT NOT NULL DEFAULT '',
    ua_version        TEXT NOT NULL DEFAULT '',
    ua_os             TEXT NOT NULL DEFAULT '',
    upstream_host     TEXT NOT NULL DEFAULT '',
    upstream_region   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS request_network_ts_idx
    ON request_network (timestamp DESC);
CREATE INDEX IF NOT EXISTS request_network_sankey_idx
    ON request_network (timestamp DESC, origin_subnet, ua_tool, upstream_host);
CREATE INDEX IF NOT EXISTS request_network_subnet_idx
    ON request_network (origin_subnet, timestamp DESC);

DO $$
BEGIN
    INSERT INTO request_network (
        seq, timestamp, remote_addr, origin_ip, origin_class, origin_subnet
    )
    SELECT l.seq,
           l.timestamp,
           NULLIF(l.client_ip, '')::inet,
           NULLIF(l.client_ip, '')::inet,
           CASE
             WHEN l.client_ip = '' THEN 'unknown'
             WHEN NULLIF(l.client_ip,'')::inet << inet '127.0.0.0/8' THEN 'loopback'
             WHEN NULLIF(l.client_ip,'')::inet << inet '10.0.0.0/8'
               OR NULLIF(l.client_ip,'')::inet << inet '172.16.0.0/12'
               OR NULLIF(l.client_ip,'')::inet << inet '192.168.0.0/16' THEN 'rfc1918'
             WHEN NULLIF(l.client_ip,'')::inet << inet '100.64.0.0/10' THEN 'cgnat'
             WHEN NULLIF(l.client_ip,'')::inet << inet '169.254.0.0/16' THEN 'link_local'
             WHEN family(NULLIF(l.client_ip,'')::inet) = 6
              AND NULLIF(l.client_ip,'')::inet << inet 'fc00::/7' THEN 'unique_local_v6'
             ELSE 'public'
           END,
           COALESCE(
             host(network(set_masklen(NULLIF(l.client_ip,'')::inet,
                  CASE WHEN family(NULLIF(l.client_ip,'')::inet)=4 THEN 24 ELSE 48 END)))
             || CASE WHEN family(NULLIF(l.client_ip,'')::inet)=4 THEN '/24' ELSE '/48' END,
             '')
      FROM ledger l
     WHERE NOT EXISTS (SELECT 1 FROM request_network r WHERE r.seq = l.seq)
       AND l.client_ip <> ''
       AND LENGTH(l.client_ip) BETWEEN 3 AND 45
       AND l.client_ip ~ '^[0-9a-fA-F:.]+$';
EXCEPTION WHEN others THEN
    RAISE NOTICE 'request_network backfill skipped: %', SQLERRM;
END $$;
