-- Per-(provider, model) USD pricing per 1k tokens, versioned by
-- `effective_from`. Cost for a ledger entry at time T uses the row with
-- MAX(effective_from) <= T_entry. EUR conversion happens at read time using
-- a single `fx_usd_eur` value from the settings table (see settings.py).
--
-- Why store USD not EUR: public LLM rates are quoted in USD; capturing them
-- as-quoted keeps the source of truth obvious. FX drift is then a one-knob
-- change in settings instead of a re-seed of this entire table.

-- created_by stores the admin user_id (no FK) so TRUNCATE CASCADE on users
-- in the test fixture doesn't wipe the pricing table out from under tests.
-- Dangling user_ids are tolerated: the only consumer (audit display) joins
-- best-effort.
CREATE TABLE IF NOT EXISTS pricing (
    provider              TEXT NOT NULL,
    model                 TEXT NOT NULL,
    prompt_usd_per_1k     NUMERIC NOT NULL CHECK (prompt_usd_per_1k     >= 0),
    completion_usd_per_1k NUMERIC NOT NULL CHECK (completion_usd_per_1k >= 0),
    effective_from        TIMESTAMPTZ NOT NULL,
    note                  TEXT NOT NULL DEFAULT '',
    created_by            BIGINT,
    PRIMARY KEY (provider, model, effective_from)
);

-- Hot lookup: "give me the row that was effective at this entry's
-- timestamp" — used by the token-cost LATERAL join.
CREATE INDEX IF NOT EXISTS pricing_lookup_idx
    ON pricing (provider, model, effective_from DESC);

-- Seed: public list prices captured around 2026-04. ON CONFLICT keeps the
-- migration idempotent for legacy databases that already ran the seed via
-- another path. Adjust via PATCH /api/pricing in the dashboard; never
-- mutate this seed in place (insert a new row with a later effective_from).
INSERT INTO pricing (provider, model, prompt_usd_per_1k, completion_usd_per_1k, effective_from, note) VALUES
    -- OpenAI list prices: https://openai.com/pricing
    ('openai',    'gpt-4o',           0.0025, 0.0100, '2026-04-01T00:00:00Z', 'seed 0005'),
    ('openai',    'gpt-4o-mini',      0.00015, 0.00060, '2026-04-01T00:00:00Z', 'seed 0005'),
    ('openai',    'gpt-4-turbo',      0.0100, 0.0300, '2026-04-01T00:00:00Z', 'seed 0005'),
    ('openai',    'gpt-4',            0.0300, 0.0600, '2026-04-01T00:00:00Z', 'seed 0005'),
    ('openai',    'gpt-3.5-turbo',    0.0005, 0.0015, '2026-04-01T00:00:00Z', 'seed 0005'),
    -- Anthropic list prices: https://www.anthropic.com/pricing
    ('anthropic', 'claude-opus-4',         0.015,  0.075,  '2026-04-01T00:00:00Z', 'seed 0005'),
    ('anthropic', 'claude-sonnet-4',       0.003,  0.015,  '2026-04-01T00:00:00Z', 'seed 0005'),
    ('anthropic', 'claude-haiku-4',        0.001,  0.005,  '2026-04-01T00:00:00Z', 'seed 0005'),
    ('anthropic', 'claude-3-5-sonnet',     0.003,  0.015,  '2026-04-01T00:00:00Z', 'seed 0005'),
    ('anthropic', 'claude-3-opus',         0.015,  0.075,  '2026-04-01T00:00:00Z', 'seed 0005'),
    -- Gemini: https://ai.google.dev/pricing
    ('gemini',    'gemini-2.0-flash',      0.000075, 0.0003, '2026-04-01T00:00:00Z', 'seed 0005'),
    ('gemini',    'gemini-1.5-pro',        0.00125, 0.005,  '2026-04-01T00:00:00Z', 'seed 0005'),
    ('gemini',    'gemini-1.5-flash',      0.000075, 0.0003, '2026-04-01T00:00:00Z', 'seed 0005')
ON CONFLICT (provider, model, effective_from) DO NOTHING;

-- Seed the EUR FX rate via the existing runtime-settings table (the
-- settings.py whitelist exposes it for admin edits). Default ~0.92 €/$
-- captured around the same date as the seed above. Operators override
-- through /api/settings without editing this file.
INSERT INTO settings (key, value, updated_at, updated_by)
VALUES ('FX_USD_EUR', '0.92', extract(epoch from now()), NULL)
ON CONFLICT (key) DO NOTHING;
