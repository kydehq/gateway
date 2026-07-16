"""DLP scanning for MCP JSON-RPC payloads.

The chat-side `dlp.scan_text` flattens already-rendered conversation
messages into a single string. MCP payloads are arbitrary JSON-RPC
envelopes — `tools/call` carries a free-form `arguments` object, results
arrive as `content[*].text` blocks, and other methods can return anything.

This module walks those payloads with a depth-bounded string extractor,
concatenates the leaves into one buffer per call (so the regex/BERT
sidecars get one HTTP fanout per request, not one per leaf), and
returns the same `DlpFinding` list the chat path already produces.

The chat-side allowlist (`dlp._apply_allowlist`) is applied here too,
so operators don't need to maintain a parallel allow list for MCP.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import httpx

from . import dlp


# Defend against pathologically nested payloads — a malicious agent could
# in principle send a JSON tree millions of levels deep and exhaust the
# stack. 20 covers every real-world MCP tool we've seen with margin.
_DEFAULT_MAX_DEPTH = 20

# Same per-call cap the chat path uses (dlp.py truncates to 8000) — keeps
# the upstream regex/BERT load bounded regardless of how much text the
# tool returned.
_MAX_TEXT_BYTES = 8000


def walk_strings(value: Any, *, max_depth: int = _DEFAULT_MAX_DEPTH) -> Iterator[str]:
    """Yield every string leaf in a JSON value.

    Numbers, booleans, and null are skipped — only string leaves carry
    natural-language content the scanners care about. Depth is bounded
    to defend against pathologically nested payloads; anything past the
    limit is silently ignored.
    """

    def _walk(v: Any, depth: int) -> Iterator[str]:
        if depth > max_depth:
            return
        if isinstance(v, str):
            if v:
                yield v
            return
        if isinstance(v, dict):
            for inner in v.values():
                yield from _walk(inner, depth + 1)
            return
        if isinstance(v, list):
            for inner in v:
                yield from _walk(inner, depth + 1)
            return
        # ints, floats, bools, None — nothing to scan.

    yield from _walk(value, 0)


def _concat(strings: Iterator[str]) -> str:
    """Join leaves with newlines, capped at the per-call byte budget."""
    buf: list[str] = []
    total = 0
    for s in strings:
        if total >= _MAX_TEXT_BYTES:
            break
        remaining = _MAX_TEXT_BYTES - total
        chunk = s if len(s) <= remaining else s[:remaining]
        buf.append(chunk)
        total += len(chunk) + 1  # +1 for the join newline
    return "\n".join(buf)


async def _scan_concatenated(text: str) -> list[dlp.DlpFinding]:
    """Run both scanners on `text` in parallel, apply the chat allowlist,
    and return the surviving findings. Mirrors `dlp.scan_text` but with
    allowlist filtering inlined so MCP findings benefit from the same
    operator rules without a second round-trip."""
    if not text.strip():
        return []
    try:
        async with httpx.AsyncClient() as client:
            tasks = []
            # Starter edition runs regex-only — skip bert entirely.
            if dlp.bert_enabled():
                tasks.append(asyncio.create_task(dlp._scan_bert(client, text)))
            tasks.append(asyncio.create_task(dlp._scan_regex(client, text)))
            raw = await asyncio.gather(*tasks)
    except Exception as exc:  # pragma: no cover — never raise out of DLP
        print(f"  ⚠ DLP [mcp]: scan failed: {exc}")
        return []

    kept: list[dlp.DlpFinding] = []
    for finding in raw:
        filtered, _suppressed = dlp._apply_allowlist(finding)
        if filtered is not None and filtered.alert:
            kept.append(filtered)
    return kept


async def scan_tool_call_params(payload: dict) -> list[dlp.DlpFinding]:
    """Scan the `arguments` of a `tools/call` request.

    The tool's `name` is not user-supplied content (it's an enum-ish
    identifier registered by the server) so we deliberately skip it; only
    the `arguments` subtree is walked.
    """
    if not isinstance(payload, dict):
        return []
    args = payload.get("arguments")
    if args is None:
        return []
    text = _concat(walk_strings(args))
    return await _scan_concatenated(text)


async def scan_tool_call_result(payload: dict) -> list[dlp.DlpFinding]:
    """Scan the `content[*].text` blocks of a `tools/call` result.

    MCP results follow the shape `{content: [{type: "text", text: ...},
    {type: "image", ...}]}`. Only text blocks are scanned; binary blocks
    (image, audio, resource) carry no natural-language content for the
    chat-tuned scanners and would just waste a regex pass.
    """
    if not isinstance(payload, dict):
        return []
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        t = block.get("text")
        if isinstance(t, str) and t:
            texts.append(t)
    text = _concat(iter(texts))
    return await _scan_concatenated(text)


async def scan_resource_read_result(payload: dict) -> list[dlp.DlpFinding]:
    """Scan `contents[*].text` from a `resources/read` result.

    Same shape rule as tool-call results, just under a different key per
    the MCP spec. Resources also carry `blob` (base64 binary) blocks
    which are skipped.
    """
    if not isinstance(payload, dict):
        return []
    contents = payload.get("contents")
    if not isinstance(contents, list):
        return []
    texts: list[str] = []
    for block in contents:
        if not isinstance(block, dict):
            continue
        t = block.get("text")
        if isinstance(t, str) and t:
            texts.append(t)
    text = _concat(iter(texts))
    return await _scan_concatenated(text)


async def scan_generic(payload: dict) -> list[dlp.DlpFinding]:
    """Fallback for unknown methods — walk every string leaf in the payload.

    Used for methods M2 doesn't have a dedicated extractor for (e.g.
    `prompts/get`, custom server-specific methods). Errs on the side of
    coverage: if the payload contains a leaked secret anywhere, the
    scanners get a shot at flagging it.
    """
    if payload is None:
        return []
    text = _concat(walk_strings(payload))
    return await _scan_concatenated(text)


async def scan_request(method: str, params: Any) -> list[dlp.DlpFinding]:
    """Dispatch a request payload (envelope.params) to the right extractor."""
    if method == "tools/call":
        return await scan_tool_call_params(params if isinstance(params, dict) else {})
    return await scan_generic(params if isinstance(params, dict) else {})


async def scan_response(method: str, result: Any) -> list[dlp.DlpFinding]:
    """Dispatch a response payload (envelope.result) to the right extractor."""
    if method == "tools/call":
        return await scan_tool_call_result(result if isinstance(result, dict) else {})
    if method == "resources/read":
        return await scan_resource_read_result(
            result if isinstance(result, dict) else {}
        )
    return await scan_generic(result if isinstance(result, dict) else {})
