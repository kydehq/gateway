-- Persistent cache of LLM-classified session intents. The frontend's
-- keyword-match classifier (lib/session-names.ts:classifyIntent) is the
-- fallback when no row exists for a session.

CREATE TABLE IF NOT EXISTS session_intents (
    session_id    TEXT PRIMARY KEY,
    intent        TEXT NOT NULL,
    confidence    NUMERIC NOT NULL DEFAULT 0,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS session_intents_classified_at_idx
    ON session_intents (classified_at DESC);
