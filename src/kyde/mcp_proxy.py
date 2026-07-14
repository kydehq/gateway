"""
MCP routing — JSON-RPC over Streamable HTTP proxy.

Scope through M2:
  * Accept HTTP POST at /mcp/{server_name} containing a JSON-RPC envelope.
  * Resolve {server_name} against the per-tenant registry.
  * Walk the envelope params through DLP (observe-only in M2 — findings
    land in dlp_alerts with source_type='mcp' but don't block).
  * Forward to the upstream over HTTP, **passing the agent's Authorization
    header through unchanged** — the gateway is transparent on upstream auth
    (credential handling is deliberately out of scope).
  * Walk the upstream result through DLP, ditto observe-only.
  * Record a signed ledger row via `mcp_ledger.record_mcp_call`.
  * Return the upstream response as-is.

Deferred (later milestones in docs/plans/mcp-routing-v1.md):
  M3  Per-tool allow/deny policy enforcement (deny → outcome='blocked').
  M4  Aggregator endpoint at /mcp/ (no name).
  --- HTTP+SSE legacy transport, stdio bridge, MCP subscriptions, streaming DLP.

JSON-RPC error codes used here:
  -32000  server not registered / disabled
  -32001  denied by per-tool policy (M3)
  -32002  upstream transport error
  -32700  malformed JSON-RPC envelope
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import dlp_json_walk, mcp_ledger, mcp_policy, mcp_registry

# `mcp_aggregator` is imported lazily inside the seed call to avoid a
# circular dependency: the aggregator handler delegates back into
# `handle_mcp_request` for tools/call routing.

# Request headers we never pass to the upstream. The first three are
# hop-by-hop / framing concerns; x-agent-id is internal-only attribution
# (matches the chat proxy in server.py).
_DROP_HEADERS = {"host", "content-length", "content-encoding", "x-agent-id"}

# Streamable HTTP response can come back as plain JSON or as SSE. For M2
# we buffer either way and return the bytes verbatim. Streaming
# pass-through is a separate milestone once buffered DLP is proven.
_UPSTREAM_TIMEOUT_S = 120.0


def _jsonrpc_error(
    code: int, message: str, request_id: Any = None, *, http_status: int = 200
) -> JSONResponse:
    """Build a JSON-RPC 2.0 error envelope.

    Per the spec, transport-level errors still use a 200 with an error
    object in the body. We follow that by default so MCP clients parse the
    payload rather than treating the response as an HTTP failure.
    """
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    return JSONResponse(body, status_code=http_status)


def _parse_envelope(raw: bytes) -> tuple[Optional[dict], Optional[JSONResponse]]:
    """Return (parsed_envelope, None) on success or (None, error_response).

    The envelope is the JSON-RPC request as a dict. We only need it to
    surface a sensible error and to know the `id` for echoing back; the
    raw bytes are what gets forwarded.
    """
    if not raw:
        return None, _jsonrpc_error(-32700, "empty request body")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, _jsonrpc_error(-32700, f"invalid JSON: {exc.msg}")
    if not isinstance(envelope, dict):
        return None, _jsonrpc_error(-32700, "JSON-RPC envelope must be an object")
    return envelope, None


def _forward_headers(request: Request) -> dict[str, str]:
    """Headers to send upstream. Authorization passes through unchanged."""
    return {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    """Headers to return to the agent. Drop framing/encoding that httpx
    has already resolved for us so we don't double-declare lengths or
    advertise an encoding the body no longer carries."""
    skip = {"content-length", "content-encoding", "transfer-encoding"}
    return {k: v for k, v in upstream.headers.items() if k.lower() not in skip}


def _try_parse_json(body: bytes) -> Optional[dict]:
    """Best-effort JSON parse of the upstream body. Returns None for SSE
    or non-JSON content so the ledger row records output_hash over the
    empty dict rather than crashing the proxy on legitimate streaming
    responses."""
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _forward_session_delete(server_name: str, request: Request) -> Response:
    """Proxy an MCP Streamable HTTP session-termination DELETE to the upstream.

    Streamable HTTP clients send a bodyless ``DELETE`` carrying ``Mcp-Session-Id``
    when they close a session (MCP spec, "Session Management"). There is no
    JSON-RPC envelope, so this bypasses the parse/DLP/ledger sandwich: we resolve
    the server and forward the DELETE so the upstream session ends cleanly,
    returning the upstream response verbatim. Teardown is transport-level, so no
    ledger row is written.

    If the server is unknown/disabled there is nothing to terminate; return 404
    so the client's "session gone" handling fires instead of retrying.
    """
    backend = mcp_registry.get_server(server_name)
    if backend is None or not backend.get("enabled", True):
        return Response(status_code=404)

    forward_headers = _forward_headers(request)
    try:
        async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
            upstream = await client.request(
                method="DELETE",
                url=backend["upstream_url"],
                headers=forward_headers,
                content=None,
            )
    except httpx.HTTPError as exc:
        # Upstream unreachable during teardown — not fatal; no session state
        # leaks through the gateway. Surface a 502 so the client knows.
        return Response(
            status_code=502,
            content=f"upstream transport error: {exc.__class__.__name__}".encode(),
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
        media_type=upstream.headers.get("content-type"),
    )


async def handle_mcp_request(server_name: str, request: Request) -> Response:
    """Entry point — wired in server.py at /mcp/{server_name}.

    M2 sandwich: parse → resolve → param DLP (observe) → forward →
    result DLP (observe) → ledger.record_mcp_call → return.

    Session-termination DELETEs carry no JSON-RPC body, so they are forwarded
    straight through before the sandwich.
    """
    if request.method == "DELETE":
        return await _forward_session_delete(server_name, request)

    raw = await request.body()
    envelope, parse_err = _parse_envelope(raw)
    if parse_err is not None:
        return parse_err
    assert envelope is not None
    request_id = envelope.get("id")
    method = str(envelope.get("method") or "")
    params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}

    backend = mcp_registry.get_server(server_name)
    if backend is None:
        return _jsonrpc_error(
            -32000, f"mcp server {server_name!r} is not registered", request_id
        )
    if not backend.get("enabled", True):
        return _jsonrpc_error(
            -32000, f"mcp server {server_name!r} is disabled", request_id
        )

    # M3: per-tool policy gate. Only `tools/call` is gated in v1 —
    # `resources/read` and metadata methods (tools/list, initialize, …)
    # pass through. The deny path writes a signed blocked ledger row so
    # auditors see the attempted call even though it never left the
    # gateway. agent_id resolution mirrors the chat-side proxy.
    if method == "tools/call":
        from .server import _agent_id  # local: server.py imports mcp_proxy

        tool_name = str(params.get("name") or "") if isinstance(params, dict) else ""
        agent_id = _agent_id(request)
        decision, reason = mcp_policy.check_policy(
            str(backend["id"]), agent_id, tool_name
        )
        if decision == "deny":
            await mcp_ledger.record_mcp_call(
                request=request,
                backend=backend,
                envelope=envelope,
                upstream_response=None,
                upstream_body=None,
                outcome="blocked",
                duration_ms=0,
                dlp_findings=[],
            )
            return _jsonrpc_error(
                -32001,
                f"denied by policy: {reason or 'no reason given'}",
                request_id,
            )

    # M2: scan request params before forwarding. Observe-only — findings
    # are recorded but the call is not blocked. A future milestone may
    # add DLP-based blocking.
    request_findings = await dlp_json_walk.scan_request(method, params)

    forward_headers = _forward_headers(request)
    upstream_url = backend["upstream_url"]

    started = time.perf_counter()
    upstream: Optional[httpx.Response] = None
    transport_error: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
            upstream = await client.request(
                method=request.method,
                url=upstream_url,
                headers=forward_headers,
                content=raw,
            )
    except httpx.HTTPError as exc:
        transport_error = exc.__class__.__name__

    duration_ms = int((time.perf_counter() - started) * 1000)

    if transport_error is not None:
        # Record the failed call so operators can spot upstream flakiness
        # in the ledger / dashboards without needing a separate metric.
        await mcp_ledger.record_mcp_call(
            request=request,
            backend=backend,
            envelope=envelope,
            upstream_response=None,
            upstream_body=None,
            outcome="upstream_error",
            duration_ms=duration_ms,
            dlp_findings=request_findings,
        )
        return _jsonrpc_error(
            -32002, f"upstream transport error: {transport_error}", request_id
        )

    assert upstream is not None
    body_bytes = upstream.content
    parsed_body = _try_parse_json(body_bytes)
    result_payload = (
        parsed_body.get("result")
        if isinstance(parsed_body, dict) and isinstance(parsed_body.get("result"), dict)
        else {}
    )
    response_findings = await dlp_json_walk.scan_response(method, result_payload)

    # M4: opportunistically seed the aggregator catalog from real
    # tools/list responses. We only seed on success (2xx + a `tools`
    # array) so a broken upstream doesn't blow away the cached snapshot.
    if (
        method == "tools/list"
        and 200 <= upstream.status_code < 300
        and isinstance(result_payload, dict)
    ):
        tools = result_payload.get("tools")
        if isinstance(tools, list):
            from . import mcp_aggregator  # local: see top-of-module note

            mcp_aggregator.seed_from_tools_list(server_name, tools)

    await mcp_ledger.record_mcp_call(
        request=request,
        backend=backend,
        envelope=envelope,
        upstream_response=upstream,
        upstream_body=parsed_body,
        outcome="ok",
        duration_ms=duration_ms,
        dlp_findings=request_findings + response_findings,
    )

    return Response(
        content=body_bytes,
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
        media_type=upstream.headers.get("content-type"),
    )
