"""Tests for kyde.dlp_json_walk — the depth-bounded JSON walker
and the per-method extractors for MCP `tools/call` / `resources/read`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kyde import dlp, dlp_json_walk

# ---------------------------------------------------------------------------
# walk_strings — the leaf extractor
# ---------------------------------------------------------------------------


def _walk(v: Any) -> list[str]:
    return list(dlp_json_walk.walk_strings(v))


def test_walk_yields_string_leaves_only():
    payload = {
        "a": "hello",
        "b": 42,
        "c": True,
        "d": None,
        "e": ["world", 1, False, None],
    }
    assert sorted(_walk(payload)) == ["hello", "world"]


def test_walk_skips_empty_strings():
    assert _walk({"a": "", "b": "kept"}) == ["kept"]


def test_walk_descends_into_nested_dicts_and_lists():
    payload = {"outer": {"inner": ["a", {"deep": "b"}]}}
    assert sorted(_walk(payload)) == ["a", "b"]


def test_walk_respects_depth_limit():
    # 25 levels of nesting; default depth is 20.
    deep: Any = "needle"
    for _ in range(25):
        deep = {"d": deep}
    out = list(dlp_json_walk.walk_strings(deep))
    assert out == []


def test_walk_under_custom_depth_yields_when_in_range():
    deep: Any = "needle"
    for _ in range(5):
        deep = {"d": deep}
    assert list(dlp_json_walk.walk_strings(deep, max_depth=10)) == ["needle"]


def test_walk_handles_scalar_root():
    assert _walk("just a string") == ["just a string"]
    assert _walk(42) == []
    assert _walk(None) == []


# ---------------------------------------------------------------------------
# Extractors — verify they pick the right subtree per MCP method
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_scanners(monkeypatch):
    """Replace the upstream DLP scanners with deterministic stubs so the
    extractor tests don't need the regex/BERT sidecars running.

    The stub returns the input text under a custom 'matched_value' key so
    each extractor test can assert on which subtree was scanned."""

    async def fake_scan_bert(client, text):
        return dlp.DlpFinding(
            scanner="bert",
            alert=False,
            score=0.0,
            findings=[],
        )

    async def fake_scan_regex(client, text):
        if not text:
            return dlp.DlpFinding(scanner="regex", alert=False, score=0.0, findings=[])
        return dlp.DlpFinding(
            scanner="regex",
            alert=True,
            score=1.0,
            findings=[
                {
                    "pattern_id": "TEST_ECHO",
                    "pattern_name": "TestEcho",
                    "entity_type": "TEST_ECHO",
                    "matched_value": text,
                    "confidence": 1.0,
                }
            ],
        )

    monkeypatch.setattr(dlp, "_scan_bert", fake_scan_bert)
    monkeypatch.setattr(dlp, "_scan_regex", fake_scan_regex)


def _regex_match_text(findings: list[dlp.DlpFinding]) -> str:
    for f in findings:
        if f.scanner == "regex" and f.alert and f.findings:
            return str(f.findings[0].get("matched_value") or "")
    return ""


def test_scan_tool_call_params_walks_arguments_only():
    # 'name' is the tool identifier; it must NOT be scanned. Only the
    # arguments subtree carries user-supplied content.
    payload = {
        "name": "secret_tool_name_should_not_be_scanned",
        "arguments": {"to": "alice@example.com", "msg": "hi"},
    }
    findings = asyncio.run(dlp_json_walk.scan_tool_call_params(payload))
    matched = _regex_match_text(findings)
    assert "secret_tool_name" not in matched
    assert "alice@example.com" in matched
    assert "hi" in matched


def test_scan_tool_call_params_missing_arguments_returns_empty():
    findings = asyncio.run(dlp_json_walk.scan_tool_call_params({"name": "x"}))
    assert findings == []


def test_scan_tool_call_result_walks_text_blocks_only():
    payload = {
        "content": [
            {"type": "text", "text": "scanned text 1"},
            {"type": "image", "data": "BASE64DATA_SHOULD_NOT_APPEAR"},
            {"type": "text", "text": "scanned text 2"},
            {"type": "resource", "uri": "file:///etc/passwd"},
        ]
    }
    findings = asyncio.run(dlp_json_walk.scan_tool_call_result(payload))
    matched = _regex_match_text(findings)
    assert "scanned text 1" in matched
    assert "scanned text 2" in matched
    assert "BASE64DATA_SHOULD_NOT_APPEAR" not in matched
    assert "/etc/passwd" not in matched


def test_scan_resource_read_result_walks_text_blocks():
    payload = {
        "contents": [
            {"uri": "file:///x", "text": "resource body"},
            {"uri": "file:///y", "blob": "BINARYBLOB"},
        ]
    }
    findings = asyncio.run(dlp_json_walk.scan_resource_read_result(payload))
    matched = _regex_match_text(findings)
    assert "resource body" in matched
    assert "BINARYBLOB" not in matched


def test_scan_generic_walks_every_string_leaf():
    payload = {"any": {"shape": ["whatever", "fields"]}}
    findings = asyncio.run(dlp_json_walk.scan_generic(payload))
    matched = _regex_match_text(findings)
    assert "whatever" in matched
    assert "fields" in matched


def test_scan_request_dispatches_by_method():
    # tools/call → arguments only
    f1 = asyncio.run(
        dlp_json_walk.scan_request(
            "tools/call",
            {"name": "leak_tool_name", "arguments": {"x": "scanned"}},
        )
    )
    assert "leak_tool_name" not in _regex_match_text(f1)
    assert "scanned" in _regex_match_text(f1)

    # other → generic walk
    f2 = asyncio.run(
        dlp_json_walk.scan_request("prompts/get", {"name": "leak_tool_name"})
    )
    assert "leak_tool_name" in _regex_match_text(f2)


def test_scan_response_dispatches_by_method():
    f1 = asyncio.run(
        dlp_json_walk.scan_response(
            "tools/call",
            {"content": [{"type": "text", "text": "alpha"}]},
        )
    )
    assert "alpha" in _regex_match_text(f1)

    f2 = asyncio.run(
        dlp_json_walk.scan_response(
            "resources/read",
            {"contents": [{"text": "beta"}]},
        )
    )
    assert "beta" in _regex_match_text(f2)

    f3 = asyncio.run(
        dlp_json_walk.scan_response("prompts/get", {"messages": ["gamma"]})
    )
    assert "gamma" in _regex_match_text(f3)


# ---------------------------------------------------------------------------
# Allowlist integration — ensure MCP path honours the chat allowlist
# ---------------------------------------------------------------------------


def test_findings_pass_through_allowlist(monkeypatch):
    """If a chat operator allowlists an entity_type, MCP findings for the
    same type must be suppressed too — we share one allow list."""

    def fake_find_and_bump(scanner, candidates, text):
        return {"id": 1, "scanner": scanner, "kind": "allow"}

    from kyde import ledger as ledger_mod

    monkeypatch.setattr(ledger_mod, "find_and_bump_allow_rule", fake_find_and_bump)

    findings = asyncio.run(
        dlp_json_walk.scan_tool_call_params({"arguments": {"email": "x@y.z"}})
    )
    # Everything got allowlisted → no findings survive.
    assert findings == []
