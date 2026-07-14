# Agent Trust Score — Signal Coverage Mapping

**Source formula:** the 5-dimension trust-score calculator
**Date:** 2026-06-16
**Question:** Do we have variables that support the 5 dimensions? Which exist, which are missing?

## TL;DR

2 of 5 dimensions are well-covered, 2 are partial, 1 (Economics) is largely gutted.

| Dimension | Layer (weight) | Coverage | Main gap |
|---|---|---|---|
| **Compliance** | Safety Gate (55%) | 🟢 Full | — |
| **Security** | Safety Gate (55%) | 🟢 Strong | injection detection |
| **Integrity** | Operational (45%) | 🟡 Partial | drift (no baseline) |
| **Reliability** | Operational (45%) | 🟡 Partial | failed requests not logged (200-only); no chat latency; no general human override |
| **Economics** | Operational (45%) | 🔴 Weak | cost deliberately removed; only tokens remain |

The two Safety-Gate dimensions (the 55% veto layer) are essentially ready today; the 45% operational layer is where the missing instrumentation sits.

---

## Formula recap

```
SafetyGate = √(Security × Compliance)          (weight 55%, veto layer)
OpScore    = 0.45×Integrity + 0.35×Reliability + 0.20×Economics   (weight 45%)
Score      = 0.55×SafetyGate + 0.45×OpScore

Hard caps:
  Security   < 20            → Score ≤ 30 (Isolation)
  Compliance < 15            → Score ≤ 25 (no audit = no trust)
  Security < 20 AND Compl<15 → Score ≤ 15
```

---

## Safety Gate (Veto layer, 55%)

### 1. Security — *Leaks, Injections, Violations*

| Sub-signal | Status | Backing variable |
|---|---|---|
| Leaks (DLP) | ✅ Strong | `dlp_alerts` (`severity`, `score`, `findings`, `disposition='confirmed_leak'`, `prevented`, `seen_count`) |
| Violations (policy blocks) | ✅ Strong | `ledger.action_type IN ('policy_block','mcp_blocked')`, `agent_blocks`, `mcp_tool_policies.decision='deny'` |
| **Injections** (prompt injection) | ❌ Missing | No semantic injection detection. OWASP regex catches credential markers, not injected instructions. BERT scanner targets secrets, not injection. |

**Verdict: ~2/3 sub-signals.** Leaks and violations are first-class; injection detection is the gap.

### 2. Compliance — *Audit Trail, Hash-chain, Policies*

| Sub-signal | Status | Backing variable |
|---|---|---|
| Hash chain | ✅ Strong | `ledger.prev_hash`, `entry_hash`, `signature` (Ed25519) |
| Chain integrity / verification | ✅ Strong | `verification_runs.chain_breaks`, `signature_failures`, `status`, `first_broken_seq` |
| Audit trail | ✅ Strong | `admin_actions` (before/after), `dlp_alert_events`, `auth_sessions`, `agent_traffic_mode_history` |
| Policies | ✅ Strong | `mcp_tool_policies`, `dlp_disabled_patterns`, `dlp_prevention_patterns` |

**Verdict: full coverage.** Strongest dimension — exactly what the ledger was built for.

---

## Operational Score (45%)

### 3. Integrity — *Drift, Tool-Patterns, Scope*

| Sub-signal | Status | Backing variable |
|---|---|---|
| Tool-patterns | ✅ Strong | `ledger.tool_calls` (incl. MCP `tool_name`, `method`, `outcome`, `duration_ms`) |
| Scope | 🟡 Partial | Enforced reactively via `mcp_tool_policies` / `agent_blocks`, but no *declared* per-agent scope to compare against |
| **Drift** | ❌ Missing | No behavioral baseline, no expected-vs-actual deviation, no drift algorithm. `agents.first_seen/last_seen` + `session_intents.intent/confidence` are the closest raw material. |

**Verdict: ~1.5/3.** Tool-patterns yes; drift must be computed from scratch (no baseline stored).

### 4. Reliability — *Success Rate, Overrides, Errors*

| Sub-signal | Status | Backing variable |
|---|---|---|
| Success rate | 🟡 Partial | Inferable from `request_kind` (`chat` vs `chat_empty_request` / `chat_streaming_partial`) — but the ledger only writes on HTTP 200, so upstream errors aren't even rows |
| Errors | 🟡 Partial | MCP errors only: `mcp_servers.last_error_status/at`, `action_type='mcp_upstream_error'`. No HTTP status stored for chat. |
| **Overrides** (human) | 🟡 Partial | Only DLP-triage dispositions (`dlp_alerts.disposition`, `reopen_count`, `dlp_alert_events`). No general agent-action approve/reject. |
| Latency | ❌ Missing for chat | Only `tool_calls.duration_ms` for MCP; no per-chat-request latency |

**Verdict: ~1.5/3.** Biggest structural issue: **failed requests aren't logged at all** (200-only write gate in `server.py`), so a true success rate can't be computed without changing what we persist.

### 5. Economics — *Cost/Task, Token-Efficiency*

| Sub-signal | Status | Backing variable |
|---|---|---|
| Token usage | ✅ Have | `ledger.prompt_tokens`, `completion_tokens` (+ aggregates in `/api/token-analysis`) |
| **Cost/Task** | ❌ Removed | The `pricing` table and USD/EUR conversion were **dropped in migration 0020** ("Cost reporting retired"). No stored cost anywhere. |
| Token-efficiency | 🟡 Derivable | Computable from tokens (e.g. tokens/turn), but nothing is stored or aggregated |

**Verdict: ~1/3.** Raw tokens exist, but "cost" was intentionally retired — this dimension needs a decision before it can drive a score.

---

## What needs to change to light up all five dimensions

Not just dashboard math — three instrumentation changes:

1. **Log non-200 responses.** Reliability is unmeasurable otherwise (the 200-only write gate in `server.py`).
2. **Reintroduce a cost basis.** Re-add pricing, or accept token-efficiency as the Economics proxy and rename the dimension.
3. **Define a baseline** for drift and a declared scope per agent (Integrity); plus decide whether prompt-injection detection is in scope for Security.

---

## Key file references

**Schema:**
- `src/kyde/migrations/sql/0001_baseline.sql` — `ledger`, `dlp_alerts`, `request_network`
- `src/kyde/migrations/sql/0004_agents_table.sql` — agent identity / first_seen / last_seen
- `src/kyde/migrations/sql/0006_verification_runs.sql` — chain verification
- `src/kyde/migrations/sql/0007_agent_blocks.sql` — agent block-list
- `src/kyde/migrations/sql/0008_session_intents.sql` — session intent classification
- `src/kyde/migrations/sql/0011_agent_traffic.sql` — traffic metering
- `src/kyde/migrations/sql/0013_mcp_routing.sql` — MCP servers + tool policies
- `src/kyde/migrations/sql/0014_dlp_disabled_patterns.sql` — pattern mute list
- `src/kyde/migrations/sql/0015_mcp_ledger_and_dlp.sql` — MCP source enrichment
- `src/kyde/migrations/sql/0016_admin_actions_and_server_health.sql` — admin audit + MCP health
- `src/kyde/migrations/sql/0018_auth_sessions.sql` — persistent sessions
- `src/kyde/migrations/sql/0019_dlp_prevention.sql` — inline DLP prevention
- `src/kyde/migrations/sql/0020_drop_pricing.sql` — **pricing/cost removed**

**Logic:**
- `src/kyde/server.py` — request classification, token extraction, tool-call parsing, ledger write gate (200-only)
- `src/kyde/ledger.py` — ledger append, hash chain, DLP alert upsert
- `src/kyde/signing.py` — Ed25519 / TPM signing
- `src/kyde/dlp.py`, `dlp_triage.py` — DLP scanning + alert lifecycle
- `src/kyde/audit_log.py` — admin action recorder
- `src/kyde/dashboard.py` — `/api/stats`, `/api/token-analysis`, `/api/agent-traffic`
