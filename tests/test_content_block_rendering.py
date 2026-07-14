"""
Tests for `render_content_blocks` — the helper that flattens Anthropic-
style multimodal/tool-calling content arrays into a single readable
string used for both ledger storage and DLP scanning.

The historical bug: only `type == "text"` blocks were preserved, so any
message that consisted purely of tool_use or tool_result blocks ended up
empty in the entry-detail dialog and invisible to the DLP scanner.
"""

from __future__ import annotations

from kyde.dlp import render_content_blocks, _extract_text_from_messages


def test_string_content_passes_through():
    assert render_content_blocks("hello world") == "hello world"


def test_empty_content_returns_empty_string():
    assert render_content_blocks("") == ""
    assert render_content_blocks([]) == ""
    assert render_content_blocks(None) == ""


def test_text_block_is_extracted():
    out = render_content_blocks([{"type": "text", "text": "hi"}])
    assert out == "hi"


def test_tool_use_block_is_rendered_with_name_and_args():
    out = render_content_blocks(
        [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "read_file",
                "input": {"path": "/etc/passwd"},
            }
        ]
    )
    assert "[tool_use: read_file(" in out
    assert "/etc/passwd" in out


def test_tool_result_string_content_is_rendered():
    out = render_content_blocks(
        [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "root:x:0:0"}]
    )
    assert out == "[tool_result: root:x:0:0]"


def test_tool_result_with_nested_blocks_extracts_text():
    """Anthropic allows tool_result.content to be a list of nested
    content blocks (when a tool returns multimodal output)."""
    out = render_content_blocks(
        [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": [
                    {"type": "text", "text": "line one"},
                    {"type": "text", "text": "line two"},
                ],
            }
        ]
    )
    assert "[tool_result: line one line two]" == out


def test_image_and_document_are_tagged():
    out = render_content_blocks(
        [
            {"type": "image", "source": {"data": "...base64..."}},
            {"type": "document"},
        ]
    )
    assert "[image]" in out
    assert "[document]" in out


def test_unknown_block_type_is_surfaced_not_dropped():
    """We want auditors to notice when the model produces a block type
    we don't yet model, rather than silently losing data."""
    out = render_content_blocks([{"type": "custom_thing", "data": "..."}])
    assert "[custom_thing]" in out


def test_mixed_blocks_concatenated_in_order():
    out = render_content_blocks(
        [
            {"type": "text", "text": "Let me look that up."},
            {"type": "tool_use", "name": "search", "input": {"q": "weather"}},
        ]
    )
    assert out.startswith("Let me look that up.")
    assert "[tool_use: search(" in out


def test_tool_args_are_truncated():
    huge = {"payload": "x" * 5000}
    out = render_content_blocks([{"type": "tool_use", "name": "noop", "input": huge}])
    # 500-char cap on args, plus the surrounding "[tool_use: noop(...)]".
    assert len(out) < 700
    assert "[tool_use: noop(" in out


def test_tool_result_is_truncated():
    huge = "y" * 5000
    out = render_content_blocks(
        [{"type": "tool_result", "tool_use_id": "t", "content": huge}]
    )
    # 1500-char cap on results.
    assert len(out) < 1700
    assert "[tool_result: " in out


def test_extract_text_pulls_tool_use_so_dlp_sees_it():
    """Regression: a message consisting only of a tool_use block must
    contribute scannable text to the DLP pipeline, not an empty
    string."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "exfiltrate",
                    "input": {"secret": "AKIAIOSFODNN7EXAMPLE"},
                }
            ],
        }
    ]
    out = _extract_text_from_messages(messages)
    assert "AKIAIOSFODNN7EXAMPLE" in out
    assert "exfiltrate" in out


def test_extract_text_pulls_tool_result_so_dlp_sees_it():
    """Tool results often carry the most sensitive data (file contents,
    API responses). They must be scannable."""
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t",
                    "content": "API_KEY=hunter2-secret",
                }
            ],
        }
    ]
    out = _extract_text_from_messages(messages)
    assert "hunter2-secret" in out
