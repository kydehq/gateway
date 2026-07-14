# Migrations

Forward-only numbered SQL files. The runner (`migrations/__init__.py`) is
called from `ledger._get_pool()` at first DB access and applies any pending
files.

## Conventions

- One file per change: `NNNN_snake_case.sql` where `NNNN` is zero-padded
  (e.g. `0007_agent_blocks.sql`). Allocate sequence numbers in PR review,
  not before, to avoid merge collisions.
- Each file runs in a single transaction. If you need actions that can't
  share a transaction (e.g. `CREATE INDEX CONCURRENTLY`), split into two files.
- Idempotent where reasonable (`IF NOT EXISTS`, `IF EXISTS`). Lets the
  baseline migration tolerate re-application against legacy databases that
  already have parts of the schema.
- No `down`/rollback files. The future Rust ports (`sqlx`, `refinery`)
  default to forward-only; we match that.
- No Python interpolation in SQL files — they must be runnable verbatim
  by future Rust migration tools.

## Adding a migration

1. Pick the next sequence number after the highest existing file.
2. Create `migrations/sql/NNNN_snake_case.sql` with the DDL/DML.
3. The runner picks it up at next process start. Apply manually with
   `python -m kyde.commands migrate` (if the helper exists) or just
   restart the gateway against the target database.
