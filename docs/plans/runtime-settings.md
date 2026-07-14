# Runtime settings via DB (scope: DLP thresholds)

## Context

The admin Settings page currently only shows **read-only** operational state.
Some config values are consumed exclusively by our own containers
(`kyde-gateway` / `kyde-api`), are meaningful for operators to tune, and
don't require touching any other service. Those are prime candidates for
being made editable at runtime through the UI.

The first batch we're moving:

| Setting | Consumer | Today | After this work |
|---|---|---|---|
| `DLP_BERT_THRESHOLD`  | `kyde-gateway` / `kyde.dlp` | env var, read once at import | resolved at scan time; DB overrides env |
| `DLP_REGEX_THRESHOLD` | `kyde-gateway` / `kyde.dlp` | env var, read once at import | resolved at scan time; DB overrides env |

Explicitly **out of scope** for this batch (both consumed by foreign
containers — moving them would need coordinated changes in `dlp-bert`):

- `HF_TOKEN`, `HF_USER_ORG`

Also out of scope (bootstrap / deploy-time by nature):

- `POSTGRES_PASSWORD`, `DATABASE_URL`
- `REGISTRY`, `GATEWAY_REPO`, `DLP_REPO`, `TAG`, `DLP_BERT_VERSION`, `DLP_REGEX_VERSION` (image selection + tags)

## Approach

Three changes, shipped together:

### 1. `settings` table (Postgres)

```sql
CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  INTEGER REFERENCES users(id)
);
```

Plain text values for now — DLP thresholds aren't secrets. Secrets get a
second column (`value_encrypted BYTEA`) later; the resolver picks whichever
is populated.

### 2. Backend: a small settings resolver

New module `src/kyde/settings.py`:

```python
def get(key: str, default: str | None = None) -> str | None:
    # 1. DB row wins
    row = ledger.get_setting(key)
    if row is not None:
        return row["value"]
    # 2. Env var
    val = os.getenv(key, "")
    if val != "":
        return val
    # 3. Caller default
    return default

def get_float(key: str, default: float) -> float: ...
def set_value(key: str, value: str, user_id: int) -> None: ...
```

The resolver caches per-process for 5 seconds so a loaded proxy doesn't
hammer Postgres on every request. Cache is invalidated on write.

In `dlp.py`, replace the module-level constants with lookups inside
`_scan_bert` / `_scan_regex` call sites (or wrap behind tiny helpers:
`bert_threshold()` / `regex_threshold()`). This is the single behavioral
change in the proxy hot path.

### 3. Admin API + UI

New endpoints (admin-gated, audit-logged):

- `GET /api/settings` → `[{ key, value, source: "db"|"env"|"default", updated_at, updated_by_username }]`
  - Only returns the keys on a whitelist (`DLP_BERT_THRESHOLD`, `DLP_REGEX_THRESHOLD`)
  - `source` tells the UI whether a DB override is in effect
- `PATCH /api/settings/{key}` body `{ value: string }`
  - Validates against a per-key schema (float in [0.0, 1.0] for thresholds)
  - Writes an audit ledger entry so changes are part of the signed chain
- `DELETE /api/settings/{key}` — clears the DB override, falls back to env/default

UI: new section on the Settings page labeled **Runtime tuning**. Two
number inputs (0.0–1.0, step 0.05) showing the current effective value
with a "source" chip (`DB` / `env` / `default`) and a "Reset to default"
link when `source === "db"`. Save triggers PATCH + toast + refetch.

## Files to touch

Backend:
- `src/kyde/ledger.py` — table creation + `get_setting` / `upsert_setting`
- `src/kyde/settings.py` — new resolver module
- `src/kyde/dlp.py` — replace module constants with resolver calls
- `src/kyde/dashboard.py` — new endpoints + whitelist

Frontend:
- `src/api/types.ts` — `SettingEntry`
- `src/api/queries.ts` — `useSettings`, `useUpdateSetting`, `useResetSetting`
- `src/pages/settings.tsx` — new "Runtime tuning" section

## Rollout

1. Backend migration: `CREATE TABLE IF NOT EXISTS settings ...` on startup.
   Empty table → everything falls back to env/defaults, so **zero
   behavior change on deploy**.
2. Ship + smoke-test that `/api/settings` returns `source: "env"` for both
   keys before anyone edits anything.
3. Switch the UI section on. Setting a value through the UI should flip
   the source to `db` on refetch, and the next DLP scan should honor it.

## Risks / notes

- **Cache TTL vs. "take effect now":** 5 s is probably fine for an audit
  dashboard but surface this in the UI: "applied within ~5 seconds".
- **Prod container fleet:** if gateway scales horizontally, each worker
  has its own in-process cache — they'll all converge within TTL.
- **Audit log:** every PATCH writes a ledger entry
  (`action_type: "setting_change"`, agent_id: "admin:<username>") so the
  signed chain records who tuned what and when. This is the key property
  that makes moving config into the DB acceptable for a security product.
- **Defence in depth:** backend validates the value range even though the
  UI does; a compromised UI shouldn't be able to push `DLP_BERT_THRESHOLD=-1`.

## Effort

~1 session:
- 2 h — migration + resolver + tests
- 1 h — endpoints + whitelist
- 1 h — UI section + wire-up
- 30 min — docs (docs/deployment.md precedence table) + smoke test
