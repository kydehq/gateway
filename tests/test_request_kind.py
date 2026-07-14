"""Unit tests for the request_kind classifier in src/kyde/server.py.

The classifier is a pure function over the same inputs ledger.append()
receives, so we exercise it directly without going through the HTTP
stack. Branch-by-branch coverage of every REQUEST_KIND_* value.
"""

from __future__ import annotations

from kyde import server


def test_policy_block_short_circuits():
    # action_type='policy_block' always wins, regardless of the other
    # signals — the block branch in server.py constructs the row
    # synthetically and the classifier shouldn't second-guess it.
    assert (
        server._request_kind(
            "policy_block",
            messages=[],
            response_body={},
            tool_calls=[],
        )
        == server.REQUEST_KIND_POLICY_BLOCK
    )


def test_tool_only_when_response_has_tool_calls_and_no_text():
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "search"}}],
                }
            }
        ]
    }
    tool_calls = [{"function": "search"}]
    assert (
        server._request_kind(
            "tool_call",
            messages=[{"role": "user", "content": "find X"}],
            response_body=response,
            tool_calls=tool_calls,
        )
        == server.REQUEST_KIND_CHAT_TOOL_ONLY
    )


def test_chat_when_response_has_text_alongside_tool_calls():
    # Mixed responses (text + tool_calls) are still "chat" — the body is
    # the load-bearing signal for the user, not the tool invocation.
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I'll search for that.",
                }
            }
        ]
    }
    assert (
        server._request_kind(
            "tool_call",
            messages=[{"role": "user", "content": "find X"}],
            response_body=response,
            tool_calls=[{"function": "search"}],
        )
        == server.REQUEST_KIND_CHAT
    )


def test_empty_request_when_messages_is_empty():
    assert (
        server._request_kind(
            "chat",
            messages=[],
            response_body={},
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT_EMPTY_REQUEST
    )


def test_empty_request_when_only_system_message():
    # A request that's only system role is effectively zero-payload chat
    # — the user hasn't said anything yet.
    assert (
        server._request_kind(
            "chat",
            messages=[{"role": "system", "content": "be helpful"}],
            response_body={},
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT_EMPTY_REQUEST
    )


def test_empty_content_when_user_message_has_blank_body():
    # Messages structurally present but every content is blank — a
    # different failure mode from chat_empty_request: the schema is
    # right, the payload is degenerate.
    assert (
        server._request_kind(
            "chat",
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "   "},
            ],
            response_body={"choices": [{"message": {"content": "ok"}}]},
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT_EMPTY_CONTENT
    )


def test_streaming_partial_when_streamed_response_has_no_text():
    # _handle_streaming stamps _streamed=True on the synthetic response
    # it builds after the stream completes. If the SSE was interrupted
    # mid-flight, the synthetic response carries no assistant text and
    # no tool_calls — surface that as its own bucket so an operator can
    # tell capture failure apart from "client never said anything".
    response = {
        "choices": [
            {"message": {"role": "assistant", "content": "", "tool_calls": []}}
        ],
        "_streamed": True,
    }
    assert (
        server._request_kind(
            "chat",
            messages=[{"role": "user", "content": "hi"}],
            response_body=response,
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT_STREAMING_PARTIAL
    )


def test_chat_when_normal_request_response():
    response = {
        "choices": [{"message": {"role": "assistant", "content": "hello back"}}]
    }
    assert (
        server._request_kind(
            "chat",
            messages=[{"role": "user", "content": "hi"}],
            response_body=response,
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT
    )


def test_chat_when_streamed_response_does_have_text():
    # Streamed but completed cleanly — should NOT be chat_streaming_partial.
    response = {
        "choices": [{"message": {"role": "assistant", "content": "yes"}}],
        "_streamed": True,
    }
    assert (
        server._request_kind(
            "chat",
            messages=[{"role": "user", "content": "hi"}],
            response_body=response,
            tool_calls=[],
        )
        == server.REQUEST_KIND_CHAT
    )
