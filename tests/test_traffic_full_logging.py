"""Phase B2 — mode cache, _should_log_for_agent gate, and the non-chat
ledger-row shape produced by _log_non_chat_entry.

End-to-end "POST a fake embedding through the proxy and watch a ledger
row appear" would need httpx mocking (respx). respx isn't a project
dependency and adding it just for this test isn't worth it — the gate
function and the entry-writer are both pure enough to verify directly.
"""

from __future__ import annotations

from unittest.mock import Mock

from kyde import ledger, server

# ---------------------------------------------------------------------------
# Mode cache: read-through, TTL, invalidation
# ---------------------------------------------------------------------------


def test_mode_cache_read_through_and_invalidation():
    ledger._clear_mode_cache()

    # First read populates the cache from DB; default is count_only.
    assert (
        ledger.get_agent_traffic_mode_cached("agent:cache1", "embedding")
        == "count_only"
    )

    # Bypass the public setter to flip the DB without touching the cache.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_traffic_mode_history (agent_id, path_kind, mode) "
                "VALUES (%s, %s, %s)",
                ("agent:cache1", "embedding", "full_logging"),
            )
        conn.commit()

    # Cache still has the stale count_only value — TTL keeps it fresh
    # for callers within the window.
    assert (
        ledger.get_agent_traffic_mode_cached("agent:cache1", "embedding")
        == "count_only"
    )

    # Public setter invalidates the entry; next read sees the new value.
    ledger.set_agent_traffic_mode(
        "agent:cache1", "embedding", "full_logging", changed_by=None
    )
    assert (
        ledger.get_agent_traffic_mode_cached("agent:cache1", "embedding")
        == "full_logging"
    )


def test_mode_cache_ttl_expires(monkeypatch):
    ledger._clear_mode_cache()

    # TTL=0 forces a refresh on every read — exercises the expiry branch
    # without sleeping.
    monkeypatch.setattr(ledger, "_MODE_CACHE_TTL_SECONDS", 0.0)

    assert (
        ledger.get_agent_traffic_mode_cached("agent:cache2", "embedding")
        == "count_only"
    )

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_traffic_mode_history (agent_id, path_kind, mode) "
                "VALUES (%s, %s, %s)",
                ("agent:cache2", "embedding", "full_logging"),
            )
        conn.commit()

    assert (
        ledger.get_agent_traffic_mode_cached("agent:cache2", "embedding")
        == "full_logging"
    )


# ---------------------------------------------------------------------------
# _should_log_for_agent semantics
# ---------------------------------------------------------------------------


def test_should_log_for_agent_chat_always_true():
    ledger._clear_mode_cache()
    # Chat short-circuits before consulting mode — an agent that's never
    # been seen still gets True. This preserves Phase A behavior verbatim.
    assert (
        server._should_log_for_agent("agent:never-seen", server.PATH_KIND_CHAT) is True
    )


def test_should_log_for_agent_non_chat_defaults_to_false():
    ledger._clear_mode_cache()
    # Default mode is count_only, so a non-chat path stays metered-only
    # without an explicit flip.
    assert (
        server._should_log_for_agent("agent:not-flipped", server.PATH_KIND_EMBEDDING)
        is False
    )


def test_should_log_for_agent_non_chat_full_logging_returns_true():
    ledger._clear_mode_cache()
    ledger.set_agent_traffic_mode(
        "agent:flipped",
        server.PATH_KIND_EMBEDDING,
        "full_logging",
        changed_by=None,
    )
    assert (
        server._should_log_for_agent("agent:flipped", server.PATH_KIND_EMBEDDING)
        is True
    )


def test_should_log_for_agent_count_only_after_flip_back():
    ledger._clear_mode_cache()
    ledger.set_agent_traffic_mode(
        "agent:flipflop",
        server.PATH_KIND_EMBEDDING,
        "full_logging",
        changed_by=None,
    )
    assert (
        server._should_log_for_agent("agent:flipflop", server.PATH_KIND_EMBEDDING)
        is True
    )
    ledger.set_agent_traffic_mode(
        "agent:flipflop",
        server.PATH_KIND_EMBEDDING,
        "count_only",
        changed_by=None,
    )
    # The setter invalidates the cache, so the next call sees the new mode.
    assert (
        server._should_log_for_agent("agent:flipflop", server.PATH_KIND_EMBEDDING)
        is False
    )


# ---------------------------------------------------------------------------
# _log_non_chat_entry — the row shape that lands when mode=full_logging
# ---------------------------------------------------------------------------


def _fake_request(user_agent: str = "test-agent/1.0"):
    """Minimal FastAPI-shaped request stub _log_non_chat_entry needs.

    The function reads client_ip via _client_ip(request) — which checks
    request.client.host — and user_agent via request.headers. We give it
    both. network_origin is gated off by default in tests so we don't
    need a fuller stub.
    """
    req = Mock()
    req.client = Mock()
    req.client.host = "10.0.0.1"
    req.headers = {"User-Agent": user_agent, "X-Forwarded-For": ""}
    req.url = Mock()
    req.url.path = "/v1/embeddings"
    req.url.hostname = "proxy.local"
    return req


def test_log_non_chat_entry_writes_embedding_row():
    req = _fake_request()
    request_body = {"model": "text-embedding-3-small", "input": "hello world"}
    response_body = {
        "object": "list",
        "data": [{"object": "embedding", "embedding": [0.1, 0.2]}],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
    }

    server._log_non_chat_entry(
        request=req,
        request_body=request_body,
        response_body=response_body,
        upstream_name="openai",
        upstream_url="https://api.openai.com/v1/embeddings",
        path_kind=server.PATH_KIND_EMBEDDING,
        agent_id="agent:embed-test",
    )

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_type, request_kind, model, upstream, "
                "       prompt_tokens, completion_tokens, "
                "       full_messages, why, tool_calls, session_id "
                "  FROM ledger WHERE agent_id = %s",
                ("agent:embed-test",),
            )
            row = cur.fetchone()

    assert row is not None
    assert row["action_type"] == "api_call"
    assert row["request_kind"] == "embedding"
    assert row["model"] == "text-embedding-3-small"
    assert row["upstream"] == "openai"
    # The embedding response carries prompt_tokens but no completion_tokens
    # — _extract_token_usage handles both shapes.
    assert row["prompt_tokens"] == 7
    assert row["completion_tokens"] == 0
    # No chat content fields on a non-chat row.
    assert row["full_messages"] == []
    assert row["why"] == []
    assert row["tool_calls"] == []
    assert row["session_id"] == ""


def test_log_non_chat_entry_models_list_with_no_model_in_request():
    """Models-list calls have no `model` in the request body — model
    should fall back to '' rather than 'unknown' (which is a chat-side
    fallback)."""
    req = _fake_request(user_agent="probe/1.0")
    request_body: dict = {}
    response_body = {
        "object": "list",
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "text-embedding-3-small", "object": "model"},
        ],
    }

    server._log_non_chat_entry(
        request=req,
        request_body=request_body,
        response_body=response_body,
        upstream_name="openai",
        upstream_url="https://api.openai.com/v1/models",
        path_kind=server.PATH_KIND_MODELS_LIST,
        agent_id="agent:models-test",
    )

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT request_kind, model FROM ledger WHERE agent_id = %s",
                ("agent:models-test",),
            )
            row = cur.fetchone()
    assert row["request_kind"] == "models_list"
    assert row["model"] == ""


def test_log_non_chat_entry_swallows_errors(monkeypatch):
    """Best-effort contract: ledger failures must not propagate. Smoke
    test by patching ledger.append to raise."""
    req = _fake_request()
    monkeypatch.setattr(
        ledger,
        "append",
        Mock(side_effect=RuntimeError("simulated DB outage")),
    )
    # Must not raise.
    server._log_non_chat_entry(
        request=req,
        request_body={"model": "x"},
        response_body={"data": []},
        upstream_name="openai",
        upstream_url="https://api.openai.com/v1/embeddings",
        path_kind=server.PATH_KIND_EMBEDDING,
        agent_id="agent:err-test",
    )
