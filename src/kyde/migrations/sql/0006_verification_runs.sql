-- Persistent log of chain-verification runs. Each /api/verify call writes
-- one row so the Compliance screen can show a real "Verification history"
-- (previously a 5-day mock array in the frontend).
--
-- The chain check is read-only against ledger but appends here, so verify
-- frequency and result are visible on the audit timeline without parsing
-- application logs.

CREATE TABLE IF NOT EXISTS verification_runs (
    run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_entries     INTEGER NOT NULL,
    verified_entries  INTEGER NOT NULL,
    chain_breaks      INTEGER NOT NULL DEFAULT 0,
    signature_failures INTEGER NOT NULL DEFAULT 0,
    first_broken_seq  BIGINT,
    signature_alg     TEXT NOT NULL DEFAULT 'ed25519',
    status            TEXT NOT NULL,   -- 'pass' | 'fail'
    -- Optional error sample (first N) — full list re-derivable from a
    -- fresh /api/verify run if needed.
    error_sample      JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS verification_runs_run_at_idx
    ON verification_runs (run_at DESC);
