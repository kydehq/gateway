-- dlp_disabled_patterns: per-tenant mute list for bundled DLP regex patterns.
--
-- Row-presence semantics: a row means the pattern is muted; absence means
-- it's active. Empty table → every bundled pattern is active. This shape
-- makes "disable" the deliberate act (the row carries who/when) and lets
-- pattern additions in dlp-patterns/ light up automatically without a
-- migration.
--
-- The gateway pushes the resulting active set to dlp-regex via
-- POST /v1/patterns/replace whenever a row is inserted or deleted.

CREATE TABLE IF NOT EXISTS dlp_disabled_patterns (
    pattern_id   TEXT PRIMARY KEY,
    disabled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_by  BIGINT REFERENCES users(id) ON DELETE SET NULL
);
