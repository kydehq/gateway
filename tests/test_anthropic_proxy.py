"""Unit tests for the Anthropic-specific proxy paths in server.py:

- `_agent_id` recognising the `x-api-key` header (Anthropic SDKs).
- `_apply_anthropic_sse_chunk` accumulating text and tool_use blocks
  from Anthropic SSE envelopes.

Both are pure functions so the tests run without the HTTP stack or DB.
"""

from __future__ import annotations

import hashlib

from kyde import server


class _FakeRequest:
    """Mimic Starlette's case-insensitive Headers via dict-with-get."""

    def __init__(self, headers: dict):
        self.headers = headers


# ---------------------------------------------------------------------------
# _agent_id — x-api-key fallback
# ---------------------------------------------------------------------------


def test_agent_id_uses_x_api_key_when_bearer_absent():
    api_key = "sk-ant-test-1234567890"
    expected_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    req = _FakeRequest({"x-api-key": api_key})
    assert server._agent_id(req) == f"agent:{expected_hash}"


def test_agent_id_same_key_hashes_same_via_bearer_or_x_api_key():
    # Signing contract invariant: the same secret must yield the same
    # agent_id no matter which header carries it.
    key = "shared-secret"
    bearer = _FakeRequest({"Authorization": f"Bearer {key}"})
    via_xkey = _FakeRequest({"x-api-key": key})
    assert server._agent_id(bearer) == server._agent_id(via_xkey)


def test_agent_id_bearer_takes_precedence_over_x_api_key():
    bearer_key = "bearer-key"
    xkey = "another-key"
    expected_hash = hashlib.sha256(bearer_key.encode()).hexdigest()[:12]
    req = _FakeRequest({"Authorization": f"Bearer {bearer_key}", "x-api-key": xkey})
    assert server._agent_id(req) == f"agent:{expected_hash}"


def test_agent_id_explicit_header_still_wins():
    req = _FakeRequest({"X-Agent-ID": "agent:explicit", "x-api-key": "ignored"})
    assert server._agent_id(req) == "agent:explicit"


def test_agent_id_unknown_when_no_credentials():
    assert server._agent_id(_FakeRequest({})) == "agent:unknown"


# ---------------------------------------------------------------------------
# _apply_anthropic_sse_chunk — text accumulation
# ---------------------------------------------------------------------------


def _drive(events: list[dict]) -> dict:
    state = server._new_anthropic_stream_state()
    for ev in events:
        server._apply_anthropic_sse_chunk(ev, state)
    return state


def test_anthropic_sse_accumulates_text_deltas():
    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 17, "output_tokens": 1}},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ", world!"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 42}},
    ]
    state = _drive(events)
    assert state["content"] == "Hello, world!"
    assert state["tool_calls"] == []
    assert state["usage"] == {"input_tokens": 17, "output_tokens": 42}


def test_anthropic_sse_reassembles_tool_use_block():
    # tool_use blocks arrive as: content_block_start (with id+name),
    # one or more input_json_delta events (partial_json string), then
    # content_block_stop. The accumulator should emit a single OpenAI-shape
    # tool_call entry with the JSON re-joined as a string in `arguments`.
    events = [
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "get_weather",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"loc":'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '"SF"}'},
        },
        {"type": "content_block_stop", "index": 1},
    ]
    state = _drive(events)
    assert state["tool_calls"] == [
        {
            "id": "toolu_abc",
            "function": {
                "name": "get_weather",
                "arguments": '{"loc":"SF"}',
            },
        }
    ]
    # Buffer must have been consumed on content_block_stop.
    assert state["tool_buffers"] == {}


def test_anthropic_sse_mixed_text_and_tool_use():
    # Text in block 0, tool_use in block 1 — both should land in the
    # right buckets without interfering.
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Thinking..."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "lookup",
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        },
        {"type": "content_block_stop", "index": 1},
    ]
    state = _drive(events)
    assert state["content"] == "Thinking..."
    assert len(state["tool_calls"]) == 1
    assert state["tool_calls"][0]["function"]["name"] == "lookup"


def test_anthropic_sse_message_delta_overrides_message_start_output_tokens():
    # message_start carries output_tokens=1 as a placeholder; message_delta
    # carries the final count. The accumulator must end up with the latter.
    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 10, "output_tokens": 1}},
        },
        {"type": "message_delta", "usage": {"output_tokens": 99}},
    ]
    state = _drive(events)
    assert state["usage"]["input_tokens"] == 10
    assert state["usage"]["output_tokens"] == 99


def test_anthropic_sse_unknown_event_types_are_ignored():
    # Forward-compat: a future Anthropic event type must not break the
    # accumulator. State should stay pristine.
    state = _drive([{"type": "ping"}, {"type": "message_stop"}])
    assert state["content"] == ""
    assert state["tool_calls"] == []
    assert state["usage"] == {}
    assert state["tool_buffers"] == {}


def test_anthropic_sse_tool_use_with_empty_input_emits_object_default():
    # If a tool_use block has no input_json_delta events at all, the
    # `arguments` string should still be valid JSON ("{}") so downstream
    # _safe_parse_args doesn't have to special-case empty inputs.
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t", "name": "noop"},
        },
        {"type": "content_block_stop", "index": 0},
    ]
    state = _drive(events)
    assert state["tool_calls"][0]["function"]["arguments"] == "{}"
