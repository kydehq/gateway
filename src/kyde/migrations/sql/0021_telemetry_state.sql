-- Persistent state for the telemetry emitter (see telemetry.py).
--
-- Single-row table. It carries three things that must survive a kyde-api
-- restart:
--
--   * hmac_salt   — the per-deploy salt that pseudonymizes gateway/tenant/
--                   session identifiers before they leave the VPC. Stable
--                   for the life of the deployment so the control plane can
--                   correlate batches from the same gateway WITHOUT ever
--                   seeing a raw ID. Generated once, on the first emit, and
--                   never rotated automatically (rotating it re-anonymizes
--                   the deployment, which is a deliberate operator action).
--   * last_sent   — epoch-seconds watermark. Each successful send advances
--                   it; the next batch covers (last_sent, now]. Persisting
--                   it here (not in process memory like SERVICE_START_TIME)
--                   is what makes the delta restart-safe — a redeploy can't
--                   re-send or skip a window.
--   * last_status / last_error — surfaced on the admin telemetry endpoints
--                   so an operator can see the outcome of the most recent
--                   attempt without scraping logs.
--
-- The `singleton` CHECK + PRIMARY KEY pins the table to exactly one row; the
-- seed INSERT ... ON CONFLICT DO NOTHING is idempotent on re-apply.

CREATE TABLE IF NOT EXISTS telemetry_state (
    singleton   BOOLEAN PRIMARY KEY DEFAULT TRUE,
    hmac_salt   BYTEA,
    last_sent   DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_status TEXT NOT NULL DEFAULT '',
    last_error  TEXT NOT NULL DEFAULT '',
    updated_at  DOUBLE PRECISION NOT NULL DEFAULT 0,
    CONSTRAINT telemetry_state_singleton CHECK (singleton)
);

INSERT INTO telemetry_state (singleton) VALUES (TRUE)
    ON CONFLICT (singleton) DO NOTHING;
