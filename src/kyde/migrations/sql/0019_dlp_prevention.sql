-- dlp_prevention_patterns: opt-in list of patterns that BLOCK requests
-- inline when the global Policy Prevention switch (DLP_REGEX_PREVENTION
-- setting) is on.
--
-- Row-presence semantics, inverted relative to dlp_disabled_patterns:
-- a row means the pattern participates in prevention; absence means
-- detect-only. Empty table → nothing blocks, even with the global
-- switch on. This makes "block on this pattern" the deliberate act
-- (the row carries who/when) so a noisy pattern can't cause a
-- surprise outage when the master switch is first flipped.
--
-- Prevention is a gateway-side decision filter — toggling rows here
-- does NOT change what gets pushed to dlp-regex (the scanner keeps
-- scanning the full active set; the gateway decides block vs alert).

CREATE TABLE IF NOT EXISTS dlp_prevention_patterns (
    pattern_id   TEXT PRIMARY KEY,
    enabled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    enabled_by   BIGINT REFERENCES users(id) ON DELETE SET NULL
);

-- Distinguish alerts whose request was BLOCKED inline (prevented) from
-- detect-only alerts raised by the post-hoc scanner.
ALTER TABLE dlp_alerts
    ADD COLUMN IF NOT EXISTS prevented BOOLEAN NOT NULL DEFAULT FALSE;
