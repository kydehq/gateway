# Customer-Side BERT — Phase Breakdown & Phase 0 Implementation Plan

**Date:** 2026-06-16
**Status:** Execution plan
**Design rationale:** `bert_incremental_learning_plan.md` (the *why*). This doc is the *how/build*.

## Pipeline facts this plan is grounded in

Confirmed by reading the current code:

- **BERT is a sidecar.** `dlp-bert` (Flask, `http://dlp-bert:8000`) exposes `POST /scan` → `{flagged, label, confidence, action}`. The model lives in that container, **not** in `kyde`. `dlp-regex` is the other sidecar. `DLP_BERT_ENABLED=false` in the sandbox edition (regex-only).
- **Scan path:** `dlp.scan_and_store_entry()` scans the *delta* messages + response, calls `dlp.scan_text()` (both sidecars in parallel, 8000-char cap), then `ledger.upsert_dlp_alert(entry_id, session_id, scanner, score, findings)` with `dedup_hash` clustering.
- **Labels already exist.** `dlp_triage.transition()` sets `disposition` on close: `confirmed_leak` / `false_positive` (human), `allowlisted` / `duplicate` (system). Every change is appended to `dlp_alert_events`. BERT findings carry a `label` but **no text span** — the analyst reviews the linked `ledger.full_messages` (4000-char/msg cap).
- **Thresholds are a runtime knob today.** `settings.get("DLP_BERT_THRESHOLD")` resolves DB-override → env → default, cached ~5s. Phase 1 extends this; it does not invent it.
- **Conventions:** sequential SQL migrations (`NNNN_name.sql`, next is **0021**); DB via `ledger._conn()` (psycopg, dict rows, `Jsonb` for JSON); the proxy hot path must never raise (fire-and-forget `asyncio.create_task`).

**Ownership split.** Gateway (`kyde`, Python — stays Python per the language split): dataset, labels, trainer orchestration, versioning, threshold calibration. Sidecar (`dlp-bert`, likely a separate repo — cross-repo moves go through git, not `cp`): `/embed` endpoint, trainable head load/infer, head hot-reload.

---

## Phase breakdown

| Phase | Goal | Key deliverables | Exit criteria | Touches hot path? |
|---|---|---|---|---|
| **0 — Instrument the dataset** | Start accumulating a labeled dataset from the existing triage stream; capture embeddings off the hot path | `dlp_training_examples`, `dlp_model_versions`, `dlp_model_eval` tables; label-capture hook in `transition()`; `/embed` on sidecar; background embedding backfill | Closing an alert writes a labeled example; embeddings populate for non-expired entries; dataset queryable | No (label hook is in triage, not proxy; embedding is background) |
| **1 — Threshold recalibration** | Improve precision/recall with no weight training | Per-pattern / per-cohort calibrated thresholds derived from the local label stream; `settings`-compatible resolver extension | Calibrated thresholds applied at scan time; measurable FP-rate drop on golden set | Read-only at scan time (already cached) |
| **2 — Local head + promotion gate** | Full customer-side incremental learning | Frozen-encoder + trainable head in sidecar; background trainer; replay buffer; golden-set + shadow gate; signed versions; hot-reload | A retrained head can be promoted only after passing the gate; rollback works | Inference swap only (gated) |
| **3 — Cohort conditioning** | Detection adapts to use-case | Cohort fed into head (feature or per-cohort head/threshold) | Cohort-conditioned decisions measurable vs flat model | Inference input |
| **4 — Counterfactual + federated** | Close the feedback loop | Shadow/uncertainty sampling of *unflagged* traffic; cohort-stratified secure aggregation; signed model push-down | Blind-spot sampling feeds dataset; federated round improves global base; push-down verified by signature | Sampling tap + gated inference swap |

Sequencing rationale: value is front-loaded (0–2 capture most of it at a fraction of the risk); each phase de-risks the next; federated is last because it's the most complex and the personalization split depends on a stable local head.

---

## Phase 0 — Implementation plan (detailed)

Goal: **start accumulating training data with near-zero risk.** No inference behavior changes. Split into 0a (labels, trivially safe) and 0b (embeddings, background).

### 0a — Schema + label capture

**Migration `0021_dlp_learning.sql`** (sketch):

```sql
-- Labeled training examples mined from the triage disposition stream.
-- Stores label + references + (later) the frozen-encoder embedding.
-- Raw text is NOT stored here; it lives transiently in ledger.full_messages.
CREATE TABLE IF NOT EXISTS dlp_training_examples (
    id            BIGSERIAL PRIMARY KEY,
    alert_id      TEXT REFERENCES dlp_alerts(alert_id) ON DELETE SET NULL,
    entry_id      TEXT,                 -- ledger entry the scan came from
    scanner       TEXT NOT NULL,        -- 'bert' | 'regex'
    pattern_id    TEXT,                 -- pattern (regex) or label (bert) that fired
    label         SMALLINT,             -- NULL=unlabeled, 1=positive, 0=negative
    source        TEXT NOT NULL,        -- 'disposition' | 'allowlist' | 'counterfactual' | 'golden'
    disposition   TEXT,                 -- raw disposition string (provenance)
    analyst_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    cohort_id     TEXT,                 -- nullable until classification lands (Phase 3)
    weight        REAL NOT NULL DEFAULT 1.0,
    embedding     BYTEA,                -- frozen-encoder vector; NULL until 0b
    embedding_dim INT,
    in_holdout    BOOLEAN NOT NULL DEFAULT FALSE,  -- golden/eval set; never trained on
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    labeled_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dlp_train_label ON dlp_training_examples (scanner, label);
CREATE INDEX IF NOT EXISTS idx_dlp_train_embed_pending
    ON dlp_training_examples (id) WHERE embedding IS NULL;

-- Model artifact registry. One active version at a time.
CREATE TABLE IF NOT EXISTS dlp_model_versions (
    version      BIGSERIAL PRIMARY KEY,
    base_hash    TEXT NOT NULL,         -- frozen encoder identity
    head_hash    TEXT,                  -- trained head artifact hash
    status       TEXT NOT NULL DEFAULT 'candidate',  -- candidate|active|rolled_back|rejected
    trained_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at  TIMESTAMPTZ,
    metrics      JSONB,
    signature    BYTEA,                 -- Ed25519 over artifact (reuse signing infra)
    notes        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dlp_model_active
    ON dlp_model_versions ((status)) WHERE status = 'active';

-- Evaluation records gating promotion.
CREATE TABLE IF NOT EXISTS dlp_model_eval (
    id           BIGSERIAL PRIMARY KEY,
    version      BIGINT REFERENCES dlp_model_versions(version) ON DELETE CASCADE,
    eval_kind    TEXT NOT NULL,         -- 'golden' | 'shadow'
    precision    REAL, recall REAL, f1 REAL, fp_rate REAL,
    sample_n     INT,
    detail       JSONB,
    decision     TEXT,                  -- 'promote' | 'reject'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Label-capture hook — one place: `dlp_triage.transition()`.** Inside the existing transaction, when `is_close` and the disposition is human (`confirmed_leak` / `false_positive`), insert a `dlp_training_examples` row from the alert's `scanner` / `pattern_id` / `entry_id` (already on the locked `dlp_alerts` row via `RETURNING *`). Mapping:

| disposition | label | note |
|---|---|---|
| `confirmed_leak` | `1` | positive |
| `false_positive` | `0` | negative |
| `allowlisted` | `0` | hard negative, `source='allowlist'` |
| `duplicate` | — | skip (don't over-weight a cluster) |

Doing it at *close* time (not scan time) keeps Phase 0 to a single hook and avoids piling up unlabeled rows; scan-time unlabeled capture arrives with counterfactual sampling in Phase 4. Capture must not break a close — wrap in try/except and log, mirroring the pipeline's never-raise discipline.

**New `ledger` helper:** `record_training_example(*, alert_id, entry_id, scanner, pattern_id, label, source, disposition, analyst_id, cohort_id=None)` — a single INSERT using `ledger._conn()`. Called from `transition()` (same transaction if practical, else best-effort after commit).

**Backfill (one-off):** mine historical closed alerts + `dlp_alert_events` for human dispositions → seed `dlp_training_examples`. Pure SQL/INSERT-SELECT; no embeddings yet.

### 0b — Embedding capture (background, off hot path)

- **Sidecar:** add `POST /embed {text}` → `{embedding: float[], dim}` to `dlp-bert` returning the frozen-encoder pooled vector. (Separate repo — coordinate via git.) Keep `/scan` unchanged so inference latency is untouched.
- **Background worker (gateway):** poll `dlp_training_examples WHERE embedding IS NULL` (uses the partial index) → fetch text from `ledger.full_messages` for that `entry_id` → call `/embed` → store `embedding` (BYTEA, e.g. `numpy.tobytes()` or pgvector later) + `embedding_dim`. Examples whose entry text has aged out are marked text-expired and skipped (honest gap, not a silent drop).
- Runs as a scheduled/background task, never in the request path. Batch + rate-limit so it doesn't starve live scans on the shared sidecar.

### Phase 0 exit criteria

- Closing an alert as `confirmed_leak`/`false_positive` writes a labeled `dlp_training_examples` row.
- Embeddings populate for non-expired entries; expired ones are flagged, not dropped silently.
- A query returns class-balanced counts (positives vs negatives) per scanner — i.e. you can *see* the dataset growing. This is the Phase 2 precondition.

### Phase 0 risks / guards

- **Hot path:** label hook is in triage (not the proxy); embedding is background. No scan-latency impact. Still, wrap both in try/except + log.
- **Privacy:** no raw text in the new tables; embeddings derived from already-retained `ledger.full_messages`. Confirm the retention/TTL story before backfilling at scale (embeddings are content-derived data).
- **Imbalance from day one:** positives (`confirmed_leak`) are rare. Index on `(scanner, label)` so the trainer can keep all positives + sample negatives later.
- **Migration safety:** all `CREATE TABLE IF NOT EXISTS`; additive only; no change to `dlp_alerts` inference columns.

---

## Suggested task order for Phase 0

1. Write `0021_dlp_learning.sql` (three tables + indexes).
2. Add `ledger.record_training_example(...)`.
3. Hook it into `dlp_triage.transition()` on human-disposition close.
4. Backfill script for historical dispositions.
5. Add `/embed` to `dlp-bert` (sidecar repo).
6. Add the background embedding worker in `kyde`.
7. Verification: close a test alert → assert a labeled row; run worker → assert embedding populated; query class balance.

Phases 1–4 get their own implementation plans once Phase 0 is landed and the dataset is visibly accumulating.
