# Trust Score — Improvement Plan

Open tasks to raise the trust score from v1 (computed from existing data only)
to a faithful implementation of the 5-dimension formula. Each task is grounded
in a `# DOC GAP` marker in `src/kyde/trust.py`.

Format per task: **Now** (what v1 computes) → **Gap** (what's missing) →
**To build** (the work).

## Per-dimension upgrades

### 1. Security — add injection detection
- **Now:** DLP leaks (severity-weighted) + policy-block ratio only; a blocked
  agent is floored to `5.0`. (`_security_score`, trust.py:140)
- **Gap:** no prompt-injection / jailbreak detection — the formula's "injection
  attempts" input is entirely missing.
- **To build:** an injection/jailbreak signal (classifier or rule pass over
  request bodies) persisted to a countable per-agent column, folded into the
  penalty alongside leaks and violations.

### 2. Compliance — per-agent, graded
- **Now:** fleet-wide pass/fail from the latest `verification_runs` row; a flat
  `85` baseline when signing is off (sandbox); `10` on a broken chain.
  (`_compliance_score`, trust.py:169)
- **Gap:** all-or-nothing and global — every agent gets the same compliance
  number, and the sandbox baseline is a guess.
- **To build:** per-agent audit-completeness (share of the agent's turns that are
  actually signed/chained) and a graded score instead of the 85/10 cliff, so it
  reacts to *this* agent's coverage.

### 3. Integrity — real drift baseline
- **Now:** tool-pattern cleanliness = `1 − (denied tool calls ÷ total tool
  activity)`. (`_integrity_score`, trust.py:185)
- **Gap:** no behavioural baseline → no real drift signal; can't distinguish "this
  agent suddenly started doing new things" from normal operation.
- **To build:** a per-agent baseline of normal tool/endpoint/volume behaviour and
  a deviation score against it (the actual "drift" dimension).

### 4. Reliability — log non-200 outcomes
- **Now:** success rate from logged errors + degraded/empty responses.
  (`_reliability_score`, trust.py:194)
- **Gap:** the ledger only writes on HTTP 200, so upstream 4xx/5xx and timeouts
  aren't rows — the success rate is optimistically biased.
- **To build:** log non-200 / upstream-error / timeout outcomes so failures are
  counted in the denominator.

### 5. Economics — real cost basis
- **Now:** pure token-efficiency proxy — tokens/turn vs. the fleet median.
  (`_economics_score`, trust.py:206)
- **Gap:** no cost basis (pricing retired in migration 0020); "efficiency vs.
  median" punishes legitimately heavy agents and carries no money meaning.
- **To build:** reintroduce per-model pricing → real cost/turn, plus a
  value/outcome signal so Economics reflects cost-effectiveness, not verbosity.

## Cross-cutting

### 6. Per-agent declared scope / budget
Several dimensions (Security violations, Economics, Integrity drift) would be far
sharper measured against a *declared* expectation per agent rather than a fleet
median. No such scope exists yet — introduce a per-agent declared scope/budget
and score deviations from it.

### 7. Revisit no-signal baselines
The neutral fallbacks (`_COMPLIANCE_NO_SIGNING=85`, `_INTEGRITY_NO_TOOLS`,
`_ECONOMICS_NO_TOKENS`, reliability `100` on no activity — trust.py:129) are
placeholders that let "no data" inflate scores. Revisit each once real signals
exist so absence of data no longer reads as good health.

## References
- Signal mapping (what backs each dimension today):
  `docs/agent_trust_score_signal_mapping.md`
- Implementation: `src/kyde/trust.py`
