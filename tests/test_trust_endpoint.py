"""
Integration test for /api/fleet-trust — exercises the real SQL queries in
`trust.fleet_trust` against the schema, plus the auth gate on the endpoint.
"""

from __future__ import annotations

import json
import time

from kyde import auth, trust, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _chat(
    agent_id: str,
    *,
    action_type: str = "chat",
    request_kind: str = "chat",
    prompt: int = 100,
    completion: int = 50,
    model: str = "gpt-4o-mini",
) -> None:
    ledger.append(
        agent_id=agent_id,
        action_type=action_type,
        model=model,
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
        prompt_tokens=prompt,
        completion_tokens=completion,
        request_kind=request_kind,
    )


def _login(client) -> None:
    # An admin must exist or the bootstrap gate (auth_middleware phase 1)
    # turns every /api/* call into 401 setup_required.
    ledger.create_user(
        username="admin1",
        email="admin1@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "admin1", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_fleet_trust_requires_auth(client):
    assert client.get("/api/fleet-trust").status_code == 401


def test_fleet_trust_scores_and_ranks_agents(client):
    # Healthy agent: clean chat traffic.
    for _ in range(5):
        _chat("agent:healthy")
    # Erroring agent: half its traffic is errors / empty responses → low reliability.
    for _ in range(2):
        _chat("agent:erroring")
    for _ in range(3):
        _chat("agent:erroring", action_type="error", request_kind="chat_empty_request")
    # Blocked agent: one entry, then on the block list → Security floored → Isolated.
    _chat("agent:blocked")
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_blocks (agent_id, blocked_at, reason) VALUES (%s, %s, %s)",
                ("agent:blocked", time.time(), "test"),
            )
        conn.commit()

    _login(client)
    resp = client.get("/api/fleet-trust?window=all")
    assert resp.status_code == 200
    data = resp.json()

    # Shape.
    assert set(data["dimensions"]) == {
        "security",
        "compliance",
        "integrity",
        "reliability",
        "economics",
    }
    assert set(data["tier_counts"]) == {
        "autonomous",
        "monitored",
        "human_approval",
        "isolated",
    }
    assert isinstance(data["trust_score"], int)
    assert data["active_agents"] == 3

    by_id = {a["agent_id"]: a for a in data["agents"]}
    assert set(by_id) == {"agent:healthy", "agent:erroring", "agent:blocked"}

    # Blocked agent trips the Security hard cap (≤30 → Human Approval tier).
    assert by_id["agent:blocked"]["cap_reason"] == "security"
    assert by_id["agent:blocked"]["score"] <= 30

    # Healthy outranks erroring outranks blocked.
    assert (
        by_id["agent:healthy"]["score"]
        > by_id["agent:erroring"]["score"]
        > by_id["agent:blocked"]["score"]
    )

    # Per-agent payload carries the full dimension breakdown.
    assert set(by_id["agent:healthy"]["dimensions"]) == set(data["dimensions"])


# NOTE: test_compliance_grades_on_per_agent_signature_coverage moved to the
# kyde-enterprise repo — it grades on per-agent signature coverage, which only
# exists when ledger.append signs rows (enterprise edition).


def test_economics_penalizes_expensive_model_at_equal_tokens():
    # Identical traffic and token counts; only the model differs. The
    # cost-weighted Economics proxy should rank the frontier-model agent lower
    # than the cheap-model agent — something raw token counts could not see.
    for _ in range(4):
        _chat("agent:cheap", model="gpt-4o-mini")
    for _ in range(4):
        _chat("agent:pricey", model="claude-opus-4-8")

    out = trust.fleet_trust(None, signing_enabled=False)
    by_id = {a["agent_id"]: a for a in out["agents"]}

    assert (
        by_id["agent:pricey"]["dimensions"]["economics"]
        < by_id["agent:cheap"]["dimensions"]["economics"]
    )


def _seed_dlp_alerts(agent_id: str, *, category: str, severity: str, n: int) -> None:
    """One ledger entry for the agent, plus n dlp_alerts whose findings carry
    the given category (e.g. 'injection' vs 'pii')."""
    _chat(agent_id)
    findings = json.dumps([{"category": category, "pattern_id": f"{category}_x"}])
    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT entry_id FROM ledger WHERE agent_id = %s ORDER BY seq DESC LIMIT 1",
                (agent_id,),
            )
            entry_id = cur.fetchone()["entry_id"]
            for i in range(n):
                tag = f"al-{agent_id}-{category}-{i}"
                cur.execute(
                    "INSERT INTO dlp_alerts (alert_id, entry_id, scanner, findings, "
                    "score, status, severity, dedup_hash, created_at, updated_at) "
                    "VALUES (%s, %s, 'regex', %s::jsonb, 0.9, 'new', %s, %s, %s, %s)",
                    (tag, entry_id, findings, severity, tag, now, now),
                )
        conn.commit()


def test_injection_alert_hits_security_harder_than_a_leak():
    # Same severity, different finding category → injection must bite harder.
    _seed_dlp_alerts("agent:inj", category="injection", severity="HIGH", n=1)
    _seed_dlp_alerts("agent:leak", category="pii", severity="HIGH", n=1)

    out = trust.fleet_trust(None, signing_enabled=False)
    by_id = {a["agent_id"]: a for a in out["agents"]}
    assert (
        by_id["agent:inj"]["dimensions"]["security"]
        < by_id["agent:leak"]["dimensions"]["security"]
    )


def test_sustained_injection_trips_security_cap():
    # Four HIGH injection attempts → Security < 20 → hard cap → Isolation-ish.
    _seed_dlp_alerts("agent:attacked", category="injection", severity="HIGH", n=4)

    out = trust.fleet_trust(None, signing_enabled=False)
    by_id = {a["agent_id"]: a for a in out["agents"]}
    agent = by_id["agent:attacked"]
    assert agent["dimensions"]["security"] < 20
    assert agent["cap_reason"] == "security"
    assert agent["score"] <= 30
