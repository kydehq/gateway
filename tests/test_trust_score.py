"""
Trust-score formula tests.

`compute_composite` is pure (no DB), so these assert it against the exact
scenarios baked into the reference trust-score calculator plus the
hard-cap and tier boundaries.
"""

from __future__ import annotations

from kyde import trust

# ---------------------------------------------------------------------------
# Calculator scenarios — (S, C, I, R, E) → expected composite score.
# Values verified against the JS `calc()` (round-half-up).
# ---------------------------------------------------------------------------


def test_healthy_agent():
    # Gesunder Agent: √(85·90)=87, op=78, 0.55·87+0.45·78 = 82.95 → 83
    out = trust.compute_composite(85, 90, 78, 82, 70)
    assert out["safety_gate"] == 87
    assert out["op_score"] == 78
    assert out["score"] == 83
    assert out["tier_key"] == "monitored"
    assert out["cap_reason"] is None


def test_security_incident_caps_at_30():
    # Security 15 (<20) trips the Security hard cap → score ≤ 30.
    out = trust.compute_composite(15, 90, 80, 85, 75)
    assert out["score"] == 30
    assert out["cap_reason"] == "security"
    assert out["tier_key"] == "human_approval"


def test_broken_audit_trail_caps_at_25():
    # Compliance 8 (<15) trips the Compliance hard cap → score ≤ 25.
    out = trust.compute_composite(88, 8, 82, 80, 72)
    assert out["score"] == 25
    assert out["cap_reason"] == "compliance"
    assert out["tier_key"] == "isolated"


def test_economic_drift_no_cap():
    # Economic Drift: √(90·92)=91, op=49 → 0.55·91+0.45·49 = 72.1 → 72.
    out = trust.compute_composite(90, 92, 40, 78, 20)
    assert out["score"] == 72
    assert out["cap_reason"] is None
    assert out["tier_key"] == "monitored"


def test_compromised_both_caps_at_15():
    # Both Security<20 AND Compliance<15 → tightest ceiling, score ≤ 15.
    out = trust.compute_composite(5, 12, 30, 60, 40)
    assert out["score"] == 15
    assert out["cap_reason"] == "security_and_compliance"
    assert out["tier_key"] == "isolated"


# ---------------------------------------------------------------------------
# Rounding + boundaries
# ---------------------------------------------------------------------------


def test_round_half_up_matches_js():
    # op = 0.45·30 + 0.35·60 + 0.20·40 = 42.5 → JS Math.round = 43 (not 42).
    # (Final score is capped to 15 here, so assert the op_score directly.)
    out = trust.compute_composite(50, 50, 30, 60, 40)
    assert out["op_score"] == 43


def test_perfect_agent_is_autonomous():
    out = trust.compute_composite(100, 100, 100, 100, 100)
    assert out["score"] == 100
    assert out["tier_key"] == "autonomous"


def test_tier_boundaries():
    # >90 strictly is Autonomous; 90 itself is Monitored.
    assert trust.tier_for(91)[0] == "autonomous"
    assert trust.tier_for(90)[0] == "monitored"
    assert trust.tier_for(60)[0] == "monitored"
    assert trust.tier_for(59)[0] == "human_approval"
    assert trust.tier_for(30)[0] == "human_approval"
    assert trust.tier_for(29)[0] == "isolated"


def test_dimensions_are_clamped():
    # Out-of-range inputs are clamped to 0–100 before scoring.
    out = trust.compute_composite(150, -20, 100, 100, 100)
    assert out["dimensions"]["security"] == 100
    assert out["dimensions"]["compliance"] == 0


def test_empty_fleet_rollup():
    roll = trust._fleet_rollup([])
    assert roll["trust_score"] == 0
    assert roll["active_agents"] == 0
    assert roll["tier_key"] == "isolated"


# ---------------------------------------------------------------------------
# _compliance_score — per-agent, graded (Task #2). Pure function, no DB.
# ---------------------------------------------------------------------------


def _compliance(signed, total, *, signing=True, run=None, max_seq=10):
    return trust._compliance_score(
        signing, run, signed=signed, total=total, max_seq=max_seq
    )


def test_compliance_signing_off_is_flat_baseline():
    # No Ed25519 in this edition → shared baseline, ignores coverage.
    assert _compliance(0, 5, signing=False) == trust._COMPLIANCE_NO_SIGNING


def test_compliance_full_coverage_is_100():
    assert _compliance(8, 8) == 100


def test_compliance_partial_coverage_grades_into_band():
    # Half-signed → floor + (100-floor)*0.5 = 70 + 15 = 85. Not a cap trip.
    assert _compliance(4, 8) == trust._COMPLIANCE_COVERAGE_FLOOR + 15.0


def test_compliance_zero_coverage_sits_at_floor_not_capped():
    # Unsigned-but-valid rows (e.g. predating signing) → floor, never a veto.
    assert _compliance(0, 8) == trust._COMPLIANCE_COVERAGE_FLOOR


def test_compliance_verified_break_vetoes_implicated_agent():
    # Break at seq 5; this agent has rows through seq 10 → tainted → veto.
    run = {"status": "fail", "chain_breaks": 1, "first_broken_seq": 5}
    assert _compliance(8, 8, run=run, max_seq=10) == trust._COMPLIANCE_BROKEN_CHAIN


def test_compliance_break_after_agents_rows_leaves_it_clean():
    # Break at seq 20; this agent's last row is seq 10 → before the break →
    # keep the graded score rather than punishing an unrelated agent.
    run = {"status": "fail", "chain_breaks": 1, "first_broken_seq": 20}
    assert _compliance(8, 8, run=run, max_seq=10) == 100


def test_compliance_failure_without_seq_vetoes_all():
    # A failure with no recorded break seq → can't prove innocence → veto.
    run = {"status": "fail", "signature_failures": 2, "first_broken_seq": None}
    assert _compliance(8, 8, run=run, max_seq=10) == trust._COMPLIANCE_BROKEN_CHAIN


def test_compliance_passing_run_does_not_veto():
    run = {
        "status": "pass",
        "chain_breaks": 0,
        "signature_failures": 0,
        "first_broken_seq": None,
    }
    assert _compliance(8, 8, run=run) == 100


def test_low_compliance_caps_composite_end_to_end():
    # A vetoed agent (compliance 10 < 15) drags the whole score under the cap.
    veto = _compliance(8, 8, run={"status": "fail", "first_broken_seq": 1}, max_seq=9)
    out = trust.compute_composite(90, veto, 90, 90, 90)
    assert out["cap_reason"] == "compliance"
    assert out["score"] <= 25


# ---------------------------------------------------------------------------
# _security_score — injection penalty (Task #1). Pure, no DB.
# ---------------------------------------------------------------------------


def _dlp_row(kind, severity, n, *, status="new", disposition=None, prevented=False):
    return {
        "kind": kind,
        "severity": severity,
        "n": n,
        "status": status,
        "disposition": disposition,
        "prevented": prevented,
    }


def test_security_injection_weighs_heavier_than_leak():
    leak = trust._security_score(100, 0, [_dlp_row("leak", "high", 1)], False)
    injection = trust._security_score(100, 0, [_dlp_row("injection", "high", 1)], False)
    assert injection < leak  # same severity, injection bites harder
    # one HIGH injection = 25 penalty → 75; one HIGH leak = 15 → 85.
    assert injection == 75
    assert leak == 85


def test_security_sustained_injection_trips_cap():
    # 4 HIGH injection attempts → 100 penalty → Security 0 (< 20 cap input).
    score = trust._security_score(100, 0, [_dlp_row("injection", "high", 4)], False)
    assert score < 20
    out = trust.compute_composite(score, 90, 90, 90, 90)
    assert out["cap_reason"] == "security"
    assert out["tier_key"] in {"human_approval", "isolated"}


def test_security_one_off_injection_only_dents():
    # A single MEDIUM injection → 12 penalty → 88, no cap.
    score = trust._security_score(100, 0, [_dlp_row("injection", "medium", 1)], False)
    assert score == 88
    out = trust.compute_composite(score, 90, 90, 90, 90)
    assert out["cap_reason"] is None


def test_security_prevented_injection_is_discounted():
    full = trust._security_score(100, 0, [_dlp_row("injection", "high", 2)], False)
    blocked = trust._security_score(
        100, 0, [_dlp_row("injection", "high", 2, prevented=True)], False
    )
    assert blocked > full  # refused-inline attempts penalised at half weight


# ---------------------------------------------------------------------------
# Economics — cost-weighted compute units (Task #5). Pure, no DB.
# ---------------------------------------------------------------------------


def test_model_tier_multiplier_buckets():
    # "mini" must win over "4o" via ordering → small tier.
    assert trust._model_tier_multiplier("gpt-4o-mini") == 1.0
    assert trust._model_tier_multiplier("gpt-4o") == 10.0
    assert trust._model_tier_multiplier("claude-3-5-sonnet") == 10.0
    assert trust._model_tier_multiplier("claude-opus-4-8") == 40.0
    assert trust._model_tier_multiplier("claude-haiku-4-5") == 1.0
    # Unmapped model → mid default (not treated as cheapest).
    assert (
        trust._model_tier_multiplier("some-new-model") == trust._ECONOMICS_TIER_DEFAULT
    )
    assert trust._model_tier_multiplier("") == trust._ECONOMICS_TIER_DEFAULT


def test_cost_units_weights_output_above_input():
    w = trust._ECONOMICS_OUTPUT_WEIGHT
    prompt_heavy = trust._cost_units(
        [{"model": "x-mini", "prompt_tokens": 100, "completion_tokens": 0}]
    )
    output_heavy = trust._cost_units(
        [{"model": "x-mini", "prompt_tokens": 0, "completion_tokens": 100}]
    )
    assert prompt_heavy == 100.0  # tier ×1, output weight irrelevant here
    assert output_heavy == 100.0 * w  # completion weighted up


def test_cost_units_scales_by_model_tier():
    def row(m):
        return [{"model": m, "prompt_tokens": 100, "completion_tokens": 0}]

    mini = trust._cost_units(row("gpt-4o-mini"))
    opus = trust._cost_units(row("claude-opus-4"))
    # Same tokens, frontier model costs 40× the mini model.
    assert opus == mini * 40.0


def test_cost_units_sums_across_models():
    units = trust._cost_units(
        [
            {
                "model": "gpt-4o-mini",
                "prompt_tokens": 10,
                "completion_tokens": 0,
            },  # 10×1
            {"model": "gpt-4o", "prompt_tokens": 0, "completion_tokens": 10},  # 4·10×10
        ]
    )
    assert units == 10.0 + (trust._ECONOMICS_OUTPUT_WEIGHT * 10.0) * 10.0


def test_economics_score_is_median_relative():
    # Leaner than median → >100 clamped to 100; pricier → below 100.
    assert trust._economics_score(50.0, 100.0) == 100  # 2× lean, clamped
    assert trust._economics_score(200.0, 100.0) == 50  # 2× the median cost
    assert trust._economics_score(0.0, 100.0) == trust._ECONOMICS_NO_TOKENS


def test_fleet_rollup_is_activity_weighted():
    # A busy healthy agent should dominate a quiet unhealthy one.
    agents = [
        {
            "requests": 100,
            "score": 90,
            "tier_key": "monitored",
            "dimensions": {
                "security": 90,
                "compliance": 90,
                "integrity": 90,
                "reliability": 90,
                "economics": 90,
            },
        },
        {
            "requests": 1,
            "score": 10,
            "tier_key": "isolated",
            "dimensions": {
                "security": 10,
                "compliance": 10,
                "integrity": 10,
                "reliability": 10,
                "economics": 10,
            },
        },
    ]
    roll = trust._fleet_rollup(agents)
    assert roll["trust_score"] > 80
    assert roll["active_agents"] == 2
    assert roll["tier_counts"]["monitored"] == 1
    assert roll["tier_counts"]["isolated"] == 1
