# Customer-Side BERT — Incremental / Continual Learning Plan

**Date:** 2026-06-16
**Status:** Design / plan
**Related:** `agent_trust_score_signal_mapping.md`
**Goal:** Let the on-prem BERT DLP detector improve over time from the customer's own triage decisions and prior classifications — incrementally, without forgetting its base knowledge, without leaving the gateway, and without poisoning itself through its own feedback loop.

## Assumptions

- "Previous classifications" = (a) analyst **dispositions** already captured in `dlp_alerts.disposition` + `dlp_alert_events` (the label stream) and (b) the tenant/agent **cohort** from the classification sketch (a conditioning feature).
- BERT runs **customer-side / on-prem**; raw content never leaves. Training is **offline/background**, decoupled from the inline scanning hot path → no conflict with the proxy's future Rust move (DB stays the boundary).
- End state = shared base improved by **federated aggregation** + a **local personalization** layer; not isolated per-customer divergence.

## The core risk this design must defeat

The detector decides which content becomes an alert, so it **shapes its own future training labels** (selection bias / feedback loop). If it stops flagging a category, no labels arrive there and the blind spot grows silently — invisible in every metric that only looks at flagged traffic. Same failure family as the policy_block poisoning fix. Every phase below carries a counterfactual-sampling countermeasure.

---

## Architectural decision: freeze the encoder, learn a light head

Do **not** full-finetune BERT inline, and do **not** do per-example online SGD (both unstable, both forget).

- **Frozen BERT encoder** (preserves language understanding → no catastrophic forgetting of the base distribution).
- **Trainable lightweight head** on top: a small MLP / linear classifier over pooled embeddings, or a LoRA/adapter layer. Cheap, fast, low memory, hot-reloadable.
- **Embedding cache as the training substrate:** run the frozen encoder once per flagged span, store the **embedding + label**, train the head on embeddings. Three wins: (1) no re-running BERT to retrain, (2) raw text can be dropped after the analyst review window — store the vector, not the prompt (privacy + GDPR posture), (3) embeddings are the same Tier-2 features the strategy already contemplates.

What learns: the **head** (per-customer), conditioned on **cohort**, with the decision **threshold** recalibrated continuously.

---

## How "previous classifications" enter the model

1. **As labels (supervised signal).** `disposition` maps to targets: `confirmed_leak` → positive, `false_positive` → negative, `allowlisted`/`duplicate` → handled separately (allowlist = hard negative for that exact span; duplicate = dropped to avoid over-weighting). `dlp_alert_events` gives the audit trail + per-analyst attribution.
2. **As a conditioning feature (cohort).** Concatenate a cohort embedding to the span embedding, OR keep a per-cohort head, OR (cheapest) per-cohort thresholds. This is what lets "a coding-assistant cohort tolerate code-shaped strings a finance-RAG cohort would not."
3. **As replay memory.** A bounded buffer of past (embedding, label) pairs — reservoir sampling for negatives, **keep all positives** (leaks are rare). Replay during each retrain prevents the head from forgetting earlier customer-specific decisions when new labels arrive.

---

## Incremental update mechanics

- **Trigger:** batched "mini-epoch" retrain of the head when N new labels accumulate or on a schedule — not continuous SGD.
- **Class imbalance:** weighted / focal loss, oversample positives, retain all positives in the replay buffer.
- **Threshold recalibration** (often the biggest, safest win): Platt scaling / isotonic regression per pattern and per cohort from the local FP/FN stream. Frequently you don't need to retrain weights at all — just move the boundary given the customer's confirmed error rates.
- **Analyst-noise handling:** track per-analyst agreement; treat a single disposition as a weak label; require N-of-M or weight by analyst reliability before a label is promoted to "gold."

---

## Don't ship a worse model: the promotion gate

Every candidate head must pass a gate before it goes live inline:

- **Golden set:** a frozen, high-confidence labeled hold-out — a local set **plus** a central golden set pushed down — that candidates are scored against. Never train on it.
- **Shadow mode:** run the candidate head in parallel with the active head on live traffic; compare agreement and flagged-volume deltas.
- **Promote only if** precision/recall don't regress on the golden set and shadow behavior is sane.
- **Rollback:** keep last-known-good head; version and **sign** model artifacts (reuse the Ed25519 signing infra) so provenance is verifiable.
- **Drift watch:** monitor label distribution + model confidence over time; abnormal drift triggers a fuller retrain or a human alert rather than silent auto-promotion.

---

## Breaking the feedback loop (counterfactual labels)

- **Shadow sampling of *unflagged* traffic:** keep a small random sample of traffic the model did *not* flag, route it for (sampled) labeling. This is the only way to discover growing blind spots.
- **Uncertainty / active sampling:** preferentially surface near-threshold spans for analyst review — the labels that most improve the boundary.
- These two sampling streams are budgeted (operator-controlled) and feed the same training table.

---

## Federated layer (later phase)

- Each gateway trains its head/adapter locally; ships **only adapter weights / gradients** upward — never content, never embeddings.
- **Cohort-stratified secure aggregation** (FedAvg + optional DP noise): aggregate within cohort so a finance-RAG base isn't diluted by coding-assistant gateways. Improved global base head pushed back down.
- **Personalization split (FedPer-style):** global base head + local fine-tuned delta. Each customer gets fleet knowledge *plus* local adaptation — this is "incremental learning integrated" across the fleet, not just within one tenant.
- Signed model artifacts so a gateway only loads a trusted pushed model.

---

## System / schema changes (grounded in current pipeline)

Current pipeline: `dlp.py` (scan, BERT + regex) → `ledger.py` (alert upsert) → `dlp_triage.py` (lifecycle) → labels in `dlp_alerts.disposition` + `dlp_alert_events`.

New, all customer-side:
- **`dlp_training_examples`** — `embedding_ref`, `pattern_id`, `label`, `source_disposition`, `analyst_id`, `weight`, `cohort_id`, `in_holdout`, `added_at`. (Stores vector ref + label, not raw text after TTL.)
- **`dlp_model_versions`** — `version`, `base_hash`, `head_hash`, `trained_at`, `status` (active/candidate/rolled_back), `metrics`, `signature`.
- **`dlp_model_eval`** — `version`, golden-set metrics, shadow metrics, decision (promote/reject).
- **Background trainer** (Python, off the hot path): reads labels → trains head → writes a candidate version → runs the gate. Scanner **hot-reloads** the active head; no proxy restart, no Rust-path entanglement.

---

## Phasing (each phase ships value and de-risks the next)

- **Phase 0 — Instrument the dataset.** Capture `{embedding, label, pattern_id, cohort, analyst}` into `dlp_training_examples` from the existing triage stream. No model change yet — just accumulate. Add the embedding cache + TTL drop of raw text.
- **Phase 1 — Threshold recalibration.** Per-pattern / per-cohort calibration from the local label stream. Cheapest, safest, immediate precision/recall gains. No weight training.
- **Phase 2 — Local head + replay + gate.** Trainable head on frozen base, replay buffer, golden-set + shadow promotion gate, signed versions, hot-reload. **Full customer-side incremental learning.**
- **Phase 3 — Cohort conditioning.** Feed cohort into the head; per-cohort behavior.
- **Phase 4 — Counterfactual sampling + federated.** Shadow/uncertainty sampling to close the feedback loop; cohort-stratified federated aggregation with push-down.

---

## Metrics to track

- Precision / recall / F1 on the golden set (per pattern, per cohort).
- Analyst-confirmed FP rate and time-to-detect for newly-seen patterns.
- Calibration error per cohort.
- Drift metrics on label distribution + confidence.
- Blind-spot estimate from the counterfactual (unflagged) sample.

## Honest tradeoffs

- Continual learning is genuinely hard; the value-to-risk ratio is **front-loaded** — Phases 1–2 (threshold + frozen-base head) capture most of the benefit at a fraction of the risk. Resist jumping to full federated finetuning.
- The feedback-loop blind spot is the subtle killer, not model accuracy. If only one safeguard ships, make it the counterfactual sample.
- Per-customer divergence vs fleet consistency is a real tension; the personalization split (Phase 4) is the resolution, but it's the most complex piece — sequence it last.
