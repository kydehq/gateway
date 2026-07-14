"""
Reliability logging — Task #4 of the trust-score improvement plan.

Before this change the proxy only wrote a ledger row on HTTP 200, so every
non-200 / timeout / upstream failure vanished and the Reliability dimension
read optimistically. `server._log_error_entry` now records those failures as
`action_type='error'` rows, which `trust._reliability_score` counts.

These tests cover:
- `_log_error_entry` writes a well-formed error row (action_type/request_kind,
  zero tokens, model carried through).
- `_http_error_kind` bucketing.
- The end-to-end effect: error rows pull reliability down, and the Economics
  proxy stays honest because tokens/turn is measured over non-error turns.
"""

from __future__ import annotations

from kyde import trust, ledger, server


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for Starlette's Request — enough for `_client_ip`
    (headers.get + .client) and the User-Agent read in `_log_error_entry`."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}
        self.client = None


def _chat(
    agent_id: str,
    *,
    action_type: str = "chat",
    request_kind: str = "chat",
    prompt: int = 100,
    completion: int = 50,
) -> None:
    ledger.append(
        agent_id=agent_id,
        action_type=action_type,
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
        prompt_tokens=prompt,
        completion_tokens=completion,
        request_kind=request_kind,
    )


def _error(agent_id: str, *, request_kind: str = "error_http_5xx") -> None:
    """Seed a failure row the way `_log_error_entry` would (zero tokens)."""
    _chat(
        agent_id,
        action_type="error",
        request_kind=request_kind,
        prompt=0,
        completion=0,
    )


# ---------------------------------------------------------------------------
# _http_error_kind
# ---------------------------------------------------------------------------


def test_http_error_kind_buckets_by_class():
    assert server._http_error_kind(404) == "error_http_4xx"
    assert server._http_error_kind(429) == "error_http_4xx"
    assert server._http_error_kind(500) == "error_http_5xx"
    assert server._http_error_kind(503) == "error_http_5xx"
    # Anything outside 4xx (e.g. an unexpected 3xx) falls to the 5xx bucket.
    assert server._http_error_kind(302) == "error_http_5xx"


# ---------------------------------------------------------------------------
# _log_error_entry
# ---------------------------------------------------------------------------


def test_log_error_entry_writes_error_row():
    server._log_error_entry(
        request=_FakeRequest(),
        request_body={"model": "gpt-4o"},
        upstream_name="openai",
        upstream_url="https://api.openai.com/v1/chat/completions",
        path_kind=server.PATH_KIND_CHAT,
        agent_id="agent:err",
        error_kind="error_http_5xx",
        status_code=503,
        detail={"error": "upstream boom"},
    )

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_type, request_kind, prompt_tokens, "
                "completion_tokens, model FROM ledger WHERE agent_id = %s",
                ("agent:err",),
            )
            rows = cur.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["action_type"] == "error"
    assert row["request_kind"] == "error_http_5xx"
    assert row["prompt_tokens"] == 0
    assert row["completion_tokens"] == 0
    assert row["model"] == "gpt-4o"


def test_log_error_entry_is_counted_by_reliability_query():
    # The error row must be visible to the trust reliability reader, which
    # filters on action_type IN ('error', 'mcp_upstream_error').
    _chat("agent:mixed")
    server._log_error_entry(
        request=_FakeRequest(),
        request_body={"model": "gpt-4o"},
        upstream_name="openai",
        upstream_url="https://api.openai.com/v1/chat/completions",
        path_kind=server.PATH_KIND_CHAT,
        agent_id="agent:mixed",
        error_kind="error_timeout",
        status_code=504,
        detail="upstream timeout",
    )

    out = trust.fleet_trust(None, signing_enabled=False)
    by_id = {a["agent_id"]: a for a in out["agents"]}
    # 2 turns, 1 error → 50% success.
    assert by_id["agent:mixed"]["dimensions"]["reliability"] == 50


# ---------------------------------------------------------------------------
# End-to-end scoring effect
# ---------------------------------------------------------------------------


def test_error_rows_lower_reliability_without_inflating_economics():
    for _ in range(8):
        _chat("agent:steady")
    # Same productive traffic, plus 4 upstream failures.
    for _ in range(6):
        _chat("agent:flaky")
    for _ in range(4):
        _error("agent:flaky")

    out = trust.fleet_trust(None, signing_enabled=False)
    by_id = {a["agent_id"]: a for a in out["agents"]}
    steady = by_id["agent:steady"]["dimensions"]
    flaky = by_id["agent:flaky"]["dimensions"]

    # Reliability: steady is clean (100); flaky is 6/10 = 60.
    assert steady["reliability"] == 100
    assert flaky["reliability"] == 60

    # Economics is measured over non-error turns, so both agents have the same
    # tokens/turn (150) and the failures don't make flaky look artificially
    # "lean". Without the non-error denominator, flaky would read leaner.
    assert flaky["economics"] == steady["economics"]
