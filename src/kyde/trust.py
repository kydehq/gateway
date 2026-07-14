"""
Agent & Fleet Trust Score.

Implements the 5-dimension trust formula (see
`docs/agent_trust_score_signal_mapping.md`) computed entirely from data we
already store. The composite math is locked; the per-dimension *inputs*
are v1 heuristics derived from existing columns.

    SafetyGate = round(√(Security × Compliance))                    # weight 55%
    OpScore    = round(0.45·Integrity + 0.35·Reliability + 0.20·Economics)  # 45%
    Score      = round(0.55·SafetyGate + 0.45·OpScore)

    Hard caps (veto):
        Security < 20 AND Compliance < 15 → Score ≤ 15
        Security < 20                     → Score ≤ 30
        Compliance < 15                  → Score ≤ 25

    Tiers: >90 Autonomous · 60–90 Monitored · 30–60 Human Approval · <30 Isolated

`compute_composite` is a pure function (unit-tested against the calculator's
own scenarios). `fleet_trust` does the SQL + aggregation, mirroring the
raw-rows-then-aggregate-in-Python style of `/api/token-analysis`.

The dimensions inherit the gaps documented in the signal-mapping doc — see the
``# DOC GAP`` markers below for what to improve in a later round (no prompt
injection detection, no cost basis, no drift baseline). Non-200 / timeout /
upstream outcomes are now logged as ``action_type='error'`` (server.py
``_log_error_entry``), so Reliability counts real failures.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Iterable

from . import ledger


# ---------------------------------------------------------------------------
# The formula (pure, no I/O — keep faithful to the HTML calculator)
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _round(x: float) -> int:
    """Round half up, matching the calculator's JS ``Math.round`` (Python's
    built-in ``round`` is banker's rounding and diverges on .5 values)."""
    return math.floor(x + 0.5)


def tier_for(score: float) -> tuple[str, str]:
    """Map a 0–100 score to (tier_key, tier_label). Thresholds match the
    calculator exactly: >90 strictly is Autonomous, 90 itself is Monitored."""
    if score > 90:
        return "autonomous", "Autonomous"
    if score >= 60:
        return "monitored", "Monitored"
    if score >= 30:
        return "human_approval", "Human Approval"
    return "isolated", "Isolated"


def compute_composite(
    security: float,
    compliance: float,
    integrity: float,
    reliability: float,
    economics: float,
) -> dict:
    """Combine the five 0–100 dimensions into a composite score + tier.

    Pure function. Returns the intermediate safety-gate / op-score values and
    the hard-cap reason (if any) so callers can show the breakdown.
    """
    s = _clamp(security)
    c = _clamp(compliance)
    i = _clamp(integrity)
    r = _clamp(reliability)
    e = _clamp(economics)

    safety_gate = _round(math.sqrt(s * c))
    op_score = _round(0.45 * i + 0.35 * r + 0.20 * e)
    score = _round(0.55 * safety_gate + 0.45 * op_score)

    # Hard caps — applied after the weighted blend. Order matters: the
    # both-low case is the tightest ceiling.
    cap_reason: str | None = None
    if s < 20 and c < 15:
        if score > 15:
            score, cap_reason = 15, "security_and_compliance"
    elif s < 20:
        if score > 30:
            score, cap_reason = 30, "security"
    elif c < 15:
        if score > 25:
            score, cap_reason = 25, "compliance"

    tier_key, tier_label = tier_for(score)
    return {
        "score": score,
        "safety_gate": safety_gate,
        "op_score": op_score,
        "cap_reason": cap_reason,
        "tier": tier_label,
        "tier_key": tier_key,
        "dimensions": {
            "security": _round(s),
            "compliance": _round(c),
            "integrity": _round(i),
            "reliability": _round(r),
            "economics": _round(e),
        },
    }


# ---------------------------------------------------------------------------
# Dimension heuristics (v1 — derived from existing columns)
# ---------------------------------------------------------------------------

# Per-alert security penalty by severity, before status/prevented modifiers.
_DLP_SEVERITY_WEIGHT = {"critical": 25.0, "high": 15.0, "medium": 8.0, "low": 3.0}
# Prompt-injection / jailbreak alerts (DLP findings with category 'injection')
# weigh heavier than a PII leak: an injection *attempt* is a direct attack on
# the agent's trust boundary. Tuned so a few high-severity hits drive the total
# penalty past 80 → Security < 20 → trips the hard cap (→ Isolation).
_INJECTION_SEVERITY_WEIGHT = {
    "critical": 40.0,
    "high": 25.0,
    "medium": 12.0,
    "low": 5.0,
}
# Closed alerts that were ruled benign barely dent the score; confirmed leaks
# (or anything still open) carry full weight.
_BENIGN_DISPOSITIONS = {"false_positive", "benign", "accepted_risk"}

# Neutral baselines used when a dimension has no signal to judge.
_COMPLIANCE_NO_SIGNING = (
    85.0  # audit trail + hash chain exist; only Ed25519 verify is gated
)
# Signing on: grade on this agent's signature coverage within a band. A fully
# signed agent reaches 100; older/unsigned-but-valid rows grade *down* toward
# the floor, but never to a cap — unsigned ≠ tampered.
_COMPLIANCE_COVERAGE_FLOOR = 70.0
# A *verified* chain/signature failure that implicates this agent → no audit =
# no trust. Below the Compliance<15 hard cap, so it forces Isolation.
_COMPLIANCE_BROKEN_CHAIN = 10.0
_INTEGRITY_NO_TOOLS = (
    90.0  # no tool activity → nothing anomalous, but can't assess drift
)
_ECONOMICS_NO_TOKENS = 80.0  # no token data to judge efficiency

# Economics is measured in price-free "compute units", not dollars (the USD
# pricing table was deliberately retired in migration 0020 — volatile to
# maintain). A compute unit captures the two cost drivers whose *ratios* are
# stable even as absolute prices drift:
#   1. Output tokens cost ~4× input (completion:prompt ≈ 4–5:1 across every
#      provider in the old 0005_pricing seed).
#   2. Model tier dominates (mini → gpt-4o → opus spans ~125× on output price);
#      a coarse 3-band multiplier captures the ordering without a price feed.
# Only the *relative* multipliers matter — Economics is scored against the fleet
# median — so these are tunable knobs, not precision claims.
_ECONOMICS_OUTPUT_WEIGHT = 4.0
# Ordered: first substring match wins, so "gpt-4o-mini"/"4o" must be checked
# before the plain "gpt-4" frontier entry.
_MODEL_TIER: tuple[tuple[str, float], ...] = (
    # small / cheap (×1)
    ("mini", 1.0),
    ("haiku", 1.0),
    ("flash", 1.0),
    ("3.5", 1.0),
    ("ollama", 1.0),
    ("llama", 1.0),
    # mid (×10)
    ("4o", 10.0),
    ("sonnet", 10.0),
    ("turbo", 10.0),
    ("1.5-pro", 10.0),
    # frontier (×40)
    ("opus", 40.0),
    ("gpt-4", 40.0),
)
_ECONOMICS_TIER_DEFAULT = 10.0  # unmapped model → mid (unknown ≠ cheapest)


def _model_tier_multiplier(model: str) -> float:
    """Coarse cost multiplier for a model name. Lowercase substring match
    against `_MODEL_TIER` (first hit wins); unmapped models fall to the mid
    default rather than being treated as cheapest."""
    name = (model or "").lower()
    for substr, mult in _MODEL_TIER:
        if substr in name:
            return mult
    return _ECONOMICS_TIER_DEFAULT


def _cost_units(model_tokens: Iterable[dict]) -> float:
    """Price-free compute units for an agent's traffic: for each model the agent
    used, weight output tokens above input and scale by the model's tier."""
    total = 0.0
    for r in model_tokens:
        prompt = int(r.get("prompt_tokens") or 0)
        completion = int(r.get("completion_tokens") or 0)
        weighted = prompt + _ECONOMICS_OUTPUT_WEIGHT * completion
        total += weighted * _model_tier_multiplier(r.get("model") or "")
    return total


def _security_score(
    agent_total: int, violations: int, dlp_rows: Iterable[dict], blocked: bool
) -> float:
    # Security = Leaks (DLP) + Injection attempts + Violations (policy blocks).
    # Each dlp row carries a `kind` ('leak' | 'injection', set by
    # `_dlp_rows_by_agent`); injection findings use the heavier weight table.
    if blocked:
        # A blocked agent is, by definition, not trusted — drop below the
        # Security<20 hard cap so the composite is forced to Isolation.
        return 5.0
    penalty = 0.0
    for row in dlp_rows:
        kind = (row.get("kind") or "leak").lower()
        weights = (
            _INJECTION_SEVERITY_WEIGHT if kind == "injection" else _DLP_SEVERITY_WEIGHT
        )
        weight = weights.get(
            (row.get("severity") or "medium").lower(), weights["medium"]
        )
        n = int(row.get("n") or 0)
        status = (row.get("status") or "new").lower()
        disposition = (row.get("disposition") or "").lower()
        if status == "closed" and disposition in _BENIGN_DISPOSITIONS:
            weight *= 0.2
        if row.get("prevented"):
            weight *= 0.5  # blocked inline (leak contained / injection refused)
        penalty += weight * n
    # Policy violations scale with how much of the agent's traffic was blocked.
    if agent_total > 0:
        penalty += 30.0 * (violations / agent_total)
    return _clamp(100.0 - penalty)


def _compliance_score(
    signing_enabled: bool,
    latest_run: dict | None,
    *,
    signed: int,
    total: int,
    max_seq: int | None,
) -> float:
    """Per-agent compliance: signature coverage, with a verified-failure veto.

    Pure function. `signed`/`total` are this agent's row counts over the window;
    `max_seq` is the agent's highest ledger seq (for break attribution). The
    hash chain itself is one shared structure, but *audit coverage* and *who a
    break implicates* are per-agent — that's what makes this honest.
    """
    if not signing_enabled:
        # Without the signing module we can't run Ed25519 chain verification.
        # The audit trail and hash chain still exist, so credit a shared
        # baseline rather than grading (no per-agent signal to grade on).
        return _COMPLIANCE_NO_SIGNING

    # Graded base: share of this agent's turns that are actually signed, mapped
    # into [FLOOR, 100]. Unsigned-but-valid rows (e.g. predating signing) pull
    # the score down without tripping a cap.
    coverage = (signed / total) if total > 0 else 0.0
    base = _COMPLIANCE_COVERAGE_FLOOR + (100.0 - _COMPLIANCE_COVERAGE_FLOOR) * coverage

    # Verified failure → trust veto, but only for agents in the broken range.
    if latest_run:
        breaks = int(latest_run.get("chain_breaks") or 0)
        sig_fail = int(latest_run.get("signature_failures") or 0)
        status = (latest_run.get("status") or "").lower()
        if status == "fail" or breaks > 0 or sig_fail > 0:
            first_broken = latest_run.get("first_broken_seq")
            # No recorded break seq → can't prove this agent is clean; veto all.
            # Otherwise only agents with entries at/after the break are tainted.
            if first_broken is None or (
                max_seq is not None and int(max_seq) >= int(first_broken)
            ):
                return _COMPLIANCE_BROKEN_CHAIN

    return _clamp(base)


def _integrity_score(tool_activity: int, blocked_tools: int) -> float:
    # DOC GAP: no behavioural baseline, so no real drift signal. v1 is
    # tool-pattern cleanliness only (share of tool calls that were denied).
    denom = tool_activity + blocked_tools
    if denom == 0:
        return _INTEGRITY_NO_TOOLS
    clean_ratio = 1.0 - (blocked_tools / denom)
    return _clamp(100.0 * clean_ratio)


def _reliability_score(agent_total: int, failures: int) -> float:
    # Non-200 / timeout / upstream-error / interrupted-stream outcomes are now
    # logged as `action_type='error'` (server.py `_log_error_entry`), so they
    # land in `failures` alongside degraded/empty responses and count against
    # the success rate. `agent_total` includes those error rows, so a failed
    # turn is in both the numerator and the denominator.
    if agent_total <= 0:
        return 100.0
    success_rate = max(0.0, (agent_total - failures) / agent_total)
    return _clamp(100.0 * success_rate)


def _economics_score(agent_cost_per_turn: float, fleet_median_cpt: float) -> float:
    # Cost-effectiveness = this agent's cost-weighted compute units per turn
    # (see `_cost_units`) relative to the fleet median. Output-weighted and
    # tier-scaled, so a frontier-model agent no longer reads "leaner" than a
    # cheap-model agent at the same raw token count. Still median-relative;
    # measuring against a *declared* budget is #6, not this task.
    if fleet_median_cpt <= 0 or agent_cost_per_turn <= 0:
        return _ECONOMICS_NO_TOKENS
    ratio = fleet_median_cpt / agent_cost_per_turn  # >1 = leaner than median
    return _clamp(100.0 * ratio)


# ---------------------------------------------------------------------------
# Data access + fleet aggregation
# ---------------------------------------------------------------------------


def _agent_activity_rows(since: float | None) -> list[dict]:
    """Per-agent traffic rollup from the ledger over the window."""
    where = "WHERE l.timestamp >= %s" if since is not None else ""
    params: list = [since] if since is not None else []
    sql = f"""
        SELECT
            l.agent_id AS agent_id,
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN l.action_type IN ('policy_block','mcp_blocked')
                              THEN 1 ELSE 0 END), 0) AS violations,
            COALESCE(SUM(CASE WHEN l.action_type IN ('error','mcp_upstream_error')
                              THEN 1 ELSE 0 END), 0) AS errors,
            COALESCE(SUM(CASE WHEN l.request_kind IN
                              ('chat_empty_request','chat_streaming_partial','chat_empty_content')
                              THEN 1 ELSE 0 END), 0) AS degraded,
            COALESCE(SUM(CASE WHEN l.action_type IN
                              ('mcp_tool_call','mcp_resources_read','mcp_call','tool_call')
                           OR jsonb_array_length(l.tool_calls) > 0
                              THEN 1 ELSE 0 END), 0) AS tool_activity,
            COALESCE(SUM(CASE WHEN l.action_type = 'mcp_blocked' THEN 1 ELSE 0 END), 0) AS blocked_tools,
            COALESCE(SUM(CASE WHEN COALESCE(l.signature, '') <> ''
                              THEN 1 ELSE 0 END), 0) AS signed,
            MAX(l.seq) AS max_seq,
            MAX(l.timestamp) AS last_seen
          FROM ledger l
          {where}
         GROUP BY l.agent_id
    """
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _agent_model_tokens(since: float | None) -> dict[str, list[dict]]:
    """Per-(agent, model) token sums over the window, grouped by agent. Feeds
    the cost-weighted Economics proxy (`_cost_units`), which needs the model
    split that the agent-level rollup flattens away."""
    where = "WHERE l.timestamp >= %s" if since is not None else ""
    params: list = [since] if since is not None else []
    sql = f"""
        SELECT l.agent_id AS agent_id,
               l.model AS model,
               COALESCE(SUM(l.prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(l.completion_tokens), 0) AS completion_tokens
          FROM ledger l
          {where}
         GROUP BY l.agent_id, l.model
    """
    out: dict[str, list[dict]] = {}
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                out.setdefault(row["agent_id"], []).append(row)
    return out


def _dlp_rows_by_agent(since: float | None) -> dict[str, list[dict]]:
    """DLP alerts grouped by the agent that produced the offending entry."""
    where = "WHERE da.created_at >= %s" if since is not None else ""
    params: list = [since] if since is not None else []
    # `kind` splits prompt-injection findings (category 'injection' inside the
    # findings JSONB) from PII leaks so Security can weigh them separately — no
    # schema change, the category lives in each persisted match object.
    sql = f"""
        SELECT l.agent_id AS agent_id,
               CASE WHEN EXISTS (
                        SELECT 1 FROM jsonb_array_elements(da.findings) f
                         WHERE f->>'category' = 'injection')
                    THEN 'injection' ELSE 'leak' END AS kind,
               da.severity AS severity,
               da.status AS status,
               da.disposition AS disposition,
               da.prevented AS prevented,
               COUNT(*) AS n
          FROM dlp_alerts da
          JOIN ledger l ON l.entry_id = da.entry_id
          {where}
         GROUP BY l.agent_id, kind, da.severity, da.status, da.disposition, da.prevented
    """
    out: dict[str, list[dict]] = {}
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                out.setdefault(row["agent_id"], []).append(row)
    return out


def _blocked_agents() -> set[str]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_id FROM agent_blocks")
            return {r["agent_id"] for r in cur.fetchall()}


def _display_names() -> dict[str, str]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_id, display_name FROM agents")
            return {
                r["agent_id"]: r["display_name"]
                for r in cur.fetchall()
                if r["display_name"]
            }


def _latest_verification_run() -> dict | None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, chain_breaks, signature_failures, first_broken_seq "
                "FROM verification_runs ORDER BY run_at DESC LIMIT 1"
            )
            return cur.fetchone()


def fleet_trust(since: float | None, *, signing_enabled: bool) -> dict:
    """Compute per-agent and fleet-wide trust over the window.

    `since` is an epoch-seconds floor (None = all time). `signing_enabled`
    selects how Compliance is derived (chain verification vs. baseline).
    """
    activity = _agent_activity_rows(since)
    dlp_by_agent = _dlp_rows_by_agent(since)
    blocked = _blocked_agents()
    names = _display_names()
    latest_run = _latest_verification_run() if signing_enabled else None

    # Economics reference point: fleet median cost-weighted units per *productive*
    # (non-error) turn. Units are output-weighted and model-tier-scaled (see
    # `_cost_units`), so model choice — the biggest real cost driver — shows up.
    # Error rows carry 0 tokens, so excluding them from the denominator keeps a
    # failing agent from reading artificially cheap.
    model_tokens = _agent_model_tokens(since)
    cost_per_turn: dict[str, float] = {}
    for a in activity:
        prod = int(a["total"]) - int(a["errors"])
        units = _cost_units(model_tokens.get(a["agent_id"], []))
        if prod > 0 and units > 0:
            cost_per_turn[a["agent_id"]] = units / prod
    fleet_median_cpt = median(cost_per_turn.values()) if cost_per_turn else 0.0

    agents: list[dict] = []
    for a in activity:
        agent_id = a["agent_id"]
        total = int(a["total"])
        errors = int(a["errors"])
        failures = errors + int(a["degraded"])

        security = _security_score(
            total,
            int(a["violations"]),
            dlp_by_agent.get(agent_id, []),
            agent_id in blocked,
        )
        compliance = _compliance_score(
            signing_enabled,
            latest_run,
            signed=int(a["signed"]),
            total=total,
            max_seq=a["max_seq"],
        )
        integrity = _integrity_score(int(a["tool_activity"]), int(a["blocked_tools"]))
        reliability = _reliability_score(total, failures)
        economics = _economics_score(cost_per_turn.get(agent_id, 0.0), fleet_median_cpt)

        composite = compute_composite(
            security, compliance, integrity, reliability, economics
        )
        agents.append(
            {
                "agent_id": agent_id,
                "display_name": names.get(agent_id),
                "score": composite["score"],
                "tier": composite["tier"],
                "tier_key": composite["tier_key"],
                "cap_reason": composite["cap_reason"],
                "dimensions": composite["dimensions"],
                "requests": total,
                "last_seen": (
                    float(a["last_seen"]) if a["last_seen"] is not None else None
                ),
            }
        )

    return {
        **_fleet_rollup(agents),
        "signing_enabled": signing_enabled,
        "agents": sorted(agents, key=lambda x: x["score"]),  # worst first
    }


def _fleet_rollup(agents: list[dict]) -> dict:
    """Activity-weighted fleet score + dimension means + tier counts."""
    tier_counts = {"autonomous": 0, "monitored": 0, "human_approval": 0, "isolated": 0}
    if not agents:
        return {
            "trust_score": 0,
            "tier": "Isolated",
            "tier_key": "isolated",
            "active_agents": 0,
            "dimensions": {
                k: 0
                for k in (
                    "security",
                    "compliance",
                    "integrity",
                    "reliability",
                    "economics",
                )
            },
            "tier_counts": tier_counts,
        }

    total_weight = sum(max(a["requests"], 1) for a in agents)
    dims = ("security", "compliance", "integrity", "reliability", "economics")
    dim_sums = {k: 0.0 for k in dims}
    score_sum = 0.0
    for a in agents:
        w = max(a["requests"], 1)
        score_sum += a["score"] * w
        for k in dims:
            dim_sums[k] += a["dimensions"][k] * w
        tier_counts[a["tier_key"]] += 1

    trust_score = _round(score_sum / total_weight)
    tier_key, tier_label = tier_for(trust_score)
    return {
        "trust_score": trust_score,
        "tier": tier_label,
        "tier_key": tier_key,
        "active_agents": len(agents),
        "dimensions": {k: _round(dim_sums[k] / total_weight) for k in dims},
        "tier_counts": tier_counts,
    }
