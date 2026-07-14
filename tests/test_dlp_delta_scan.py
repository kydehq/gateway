"""
Regression test for the delta-only DLP scan fix.

Before this fix, the scanner received the full LLM context on every
turn, so a single user-side leak in turn 1 re-triggered the scanner on
every subsequent turn (the dedup_hash drifts as the context grows and
the pipeline picks up incidental new matches). After the fix,
`scan_and_store_entry` only sends the messages appended since the
prior entry in the same session.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch, AsyncMock

from kyde import dlp, ledger


def _append_entry(session_id: str, full_messages: list[dict]) -> ledger.LedgerEntry:
    """Insert one ledger entry in `session_id` carrying `full_messages`."""
    return ledger.append(
        agent_id="agent:test",
        action_type="chat",
        model="gpt-4o",
        request_body={"messages": full_messages},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[],
        tool_calls=[],
        session_id=session_id,
        upstream="openai",
        full_messages=full_messages,
    )


def _run_scan(
    entry: ledger.LedgerEntry, messages: list[dict], session_id: str
) -> list[str]:
    """Mock scan_text and call scan_and_store_entry; return what got scanned."""
    captured: list[str] = []

    async def fake_scan(text: str) -> list[Any]:
        captured.append(text)
        return []

    async def _go():
        with patch.object(dlp, "scan_text", AsyncMock(side_effect=fake_scan)):
            await dlp.scan_and_store_entry(
                entry_id=entry.entry_id,
                session_id=session_id,
                seq=entry.seq,
                messages=messages,
                response_body={"choices": [{"message": {"content": "ok"}}]},
            )

    asyncio.run(_go())
    return captured


def test_scan_uses_only_delta_messages():
    """Second entry in the same session must scan only the new turn,
    not the carried-over history."""
    session_id = "test-session-delta"
    secret = "AKIAIOSFODNN7EXAMPLE"

    turn1 = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": f"My AWS key is {secret}"},
    ]
    entry1 = _append_entry(session_id, turn1)

    turn2 = turn1 + [
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "What's the weather like?"},
    ]
    entry2 = _append_entry(session_id, turn2)

    captured1 = _run_scan(entry1, turn1, session_id)
    captured2 = _run_scan(entry2, turn2, session_id)

    assert len(captured1) == 1 and len(captured2) == 1
    # Turn 1: secret was in the user message → scanner saw it.
    assert secret in captured1[0]
    # Turn 2: secret was NOT in the appended messages → scanner did NOT see it.
    assert secret not in captured2[0]
    # And turn 2 still scanned the new user turn that was appended.
    assert "weather" in captured2[0]


def test_scan_first_entry_in_session_scans_everything():
    """First entry has no prior context, so the full messages list is
    the delta — the scanner sees everything."""
    session_id = "test-session-first"
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "leak: hunter2"},
    ]
    entry = _append_entry(session_id, messages)

    captured = _run_scan(entry, messages, session_id)

    assert len(captured) == 1
    assert "hunter2" in captured[0]
    assert "system prompt" in captured[0]


def test_scan_detects_new_secret_in_later_turn():
    """A secret that appears for the first time in turn N must still be
    scanned (it's in the delta). Guards against an over-eager fix that
    would skip later content."""
    session_id = "test-session-later"
    benign_turn = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What time is it?"},
    ]
    _append_entry(session_id, benign_turn)

    later_secret = "BEGIN_PGP_PRIVATE_KEY_BLOCK"
    turn2 = benign_turn + [
        {"role": "assistant", "content": "I don't know."},
        {"role": "user", "content": f"Anyway, here's my key: {later_secret}"},
    ]
    entry2 = _append_entry(session_id, turn2)

    captured = _run_scan(entry2, turn2, session_id)

    assert len(captured) == 1
    assert later_secret in captured[0]
    # The earlier benign turn shouldn't be re-scanned.
    assert "What time is it" not in captured[0]
