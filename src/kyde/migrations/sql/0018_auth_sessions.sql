-- Persistent dashboard auth-session store.
--
-- Before this migration, SESSION_TOKENS lived only in the kyde-api
-- process. Every restart wiped every browser session, forcing a global
-- re-login on every redeploy. Auth sessions move to Postgres so a
-- cookie outlives a container restart.
--
-- Table is named `auth_sessions` to disambiguate from the pre-existing
-- `sessions` table (migration 0003), which tracks per-conversation
-- session IDs on ledger rows — a different concept that just happens to
-- share the word.
--
-- Schema mirrors what the in-memory dict carried (username, roles,
-- must_change_password) so the middleware's per-request lookup stays
-- single-table. Roles + must_change_password are also stored in users,
-- but we keep them denormalised here so a session lookup never needs a
-- JOIN. _refresh_session() is the explicit re-sync hook for role/flag
-- changes that should land mid-session.
--
-- expires_at supersedes the cookie max-age as the source of truth; the
-- get_session helper filters by expires_at > now() so stale rows can
-- linger without affecting correctness.

CREATE TABLE IF NOT EXISTS auth_sessions (
    token                TEXT PRIMARY KEY,
    user_id              BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username             TEXT NOT NULL,
    roles                JSONB NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_sessions_user_id_idx ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx ON auth_sessions(expires_at);
