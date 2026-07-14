"""
Bare-`/mcp/` aggregator — serves the union of every backend's tools and
routes `tools/call` by `{server}__{tool}` namespace.

Design constraints:

  * The agent's `Authorization` header is forwarded unchanged. We never
    cache or refresh credentials. `tools/list` is served from the
    in-memory catalog (no upstream fanout, so no credential needed at
    aggregator-call time); `tools/call` routes to exactly one backend
    via the existing per-server proxy path, which forwards the bearer.
  * The catalog is per-tenant, 5-minute TTL, seeded opportunistically
    from real `tools/list` responses passing through the per-server
    proxy (and from the dashboard probe-tools endpoint, which already
    holds an operator-supplied one-off bearer).
  * Routing uses a `{server}__{tool}` namespace. Tool-call rewrites
    strip the prefix before delegating: the downstream proxy then signs
    / DLPs / forwards as if the agent had called /mcp/{server} directly,
    so the M2 ledger row carries the un-namespaced tool name.

Out of scope for v1: cross-tenant catalogs (we use DEFAULT_TENANT only),
auto-refresh, `resources/*` aggregation, streaming pass-through.
"""

from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import mcp_proxy, mcp_registry

# Per-tenant catalog. Outer key = tenant_id, inner key = namespaced tool
# name ("github__search_repositories"), value = (server_name, raw_tool_dict).
# We bind to seed_time so we can compute "oldest entry age" for the
# dashboard banner without scanning everything.
_CATALOG_TTL_S = 300.0

# tenant_id -> {namespaced_tool: (server_name, raw_tool, seed_monotonic)}
_catalog: dict[str, dict[str, tuple[str, dict, float]]] = {}
_catalog_lock = Lock()

_NAMESPACE_DELIM = "__"


def _ns(server_name: str, tool_name: str) -> str:
    """Build the namespaced tool name used in the aggregator catalog."""
    return f"{server_name}{_NAMESPACE_DELIM}{tool_name}"


def _split_ns(namespaced: str) -> Optional[tuple[str, str]]:
    """Inverse of `_ns`. Returns None when the input has no namespace marker
    or when either half is empty."""
    if _NAMESPACE_DELIM not in namespaced:
        return None
    server, _, tool = namespaced.partition(_NAMESPACE_DELIM)
    if not server or not tool:
        return None
    return (server, tool)


def invalidate_cache(tenant_id: Optional[str] = None) -> None:
    """Drop the catalog for one tenant, or everything when `tenant_id is None`."""
    with _catalog_lock:
        if tenant_id is None:
            _catalog.clear()
        else:
            _catalog.pop(tenant_id, None)


def _gc(tenant_id: str) -> None:
    """Evict catalog entries older than the TTL. Cheap O(n) scan since the
    catalog stays small (tens of tools per server, single-digit servers)."""
    now = time.monotonic()
    bucket = _catalog.get(tenant_id)
    if not bucket:
        return
    stale = [
        k for k, (_s, _t, seeded) in bucket.items() if now - seeded > _CATALOG_TTL_S
    ]
    for k in stale:
        bucket.pop(k, None)


def seed_from_tools_list(
    server_name: str, tools: list[dict], tenant_id: str = mcp_registry.DEFAULT_TENANT
) -> None:
    """Replace this server's entries with a fresh snapshot.

    Called from the per-server proxy after a successful tools/list, and
    from the dashboard probe-tools endpoint after the operator runs a
    one-off probe. `tools` is the MCP-spec list of tool dicts (each with
    at least a `name`; `description` and `inputSchema` ride along).

    "Replace" means an empty `tools` list clears that server's slice —
    important for a server that loses a tool between probes.
    """
    if not server_name:
        return
    now = time.monotonic()
    with _catalog_lock:
        bucket = _catalog.setdefault(tenant_id, {})
        # Drop every entry previously seeded by this server before inserting
        # the fresh snapshot. Otherwise a tool the upstream just removed
        # would linger in the catalog until TTL expiry.
        for k in [k for k, (s, _t, _seeded) in bucket.items() if s == server_name]:
            bucket.pop(k, None)
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            bucket[_ns(server_name, name)] = (server_name, tool, now)


def catalog_snapshot(tenant_id: str = mcp_registry.DEFAULT_TENANT) -> dict[str, Any]:
    """Read-only view used by the dashboard banner.

    Returns: {tools: [...], server_count: N, oldest_seconds: float|null}.
    Each tool is the raw upstream dict with its `name` rewritten to the
    namespaced form so the UI shows what the agent would see.
    """
    with _catalog_lock:
        _gc(tenant_id)
        bucket = _catalog.get(tenant_id, {})
        now = time.monotonic()
        items = []
        servers = set()
        oldest_seeded: Optional[float] = None
        for namespaced, (server, tool, seeded) in bucket.items():
            servers.add(server)
            t = dict(tool)
            t["name"] = namespaced
            items.append(
                {"server_name": server, "tool": t, "age_seconds": now - seeded}
            )
            oldest_seeded = (
                seeded if oldest_seeded is None else min(oldest_seeded, seeded)
            )
    items.sort(key=lambda x: x["tool"]["name"])
    return {
        "items": items,
        "server_count": len(servers),
        "tool_count": len(items),
        "oldest_seconds": (now - oldest_seeded) if oldest_seeded is not None else None,
    }


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------


def _aggregator_tools_list_payload(
    request_id: Any, tenant_id: str = mcp_registry.DEFAULT_TENANT
) -> dict:
    """Build the JSON-RPC result body for tools/list. The catalog is the
    union of every enabled server's tools, namespaced."""
    snap = catalog_snapshot(tenant_id)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"tools": [item["tool"] for item in snap["items"]]},
    }


async def handle_aggregator_request(
    request: Request, tenant_id: str = mcp_registry.DEFAULT_TENANT
) -> Response:
    """Entry point — wired in server.py at the bare `/mcp` and `/mcp/`."""
    # The aggregator is a gateway-synthesized server with no upstream session of
    # its own, so a Streamable HTTP session-termination DELETE has nothing to
    # forward — accept it as a no-op so clients don't see a 404 on teardown.
    if request.method == "DELETE":
        return Response(status_code=204)

    raw = await request.body()
    envelope, parse_err = mcp_proxy._parse_envelope(raw)
    if parse_err is not None:
        return parse_err
    assert envelope is not None

    request_id = envelope.get("id")
    method = str(envelope.get("method") or "")
    params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}

    if method == "tools/list":
        return JSONResponse(_aggregator_tools_list_payload(request_id, tenant_id))

    if method == "tools/call":
        name = ""
        if isinstance(params, dict):
            name = str(params.get("name") or "")
        split = _split_ns(name)
        if split is None:
            return mcp_proxy._jsonrpc_error(
                -32602,
                "aggregator tools/call requires a namespaced name "
                f"({{server}}__{{tool}}); got {name!r}",
                request_id,
            )
        server_name, raw_tool = split

        # Verify the server is still in the registry — catches stale
        # catalog entries with a real 404 instead of a forward error.
        if mcp_registry.get_server(server_name, tenant_id) is None:
            return mcp_proxy._jsonrpc_error(
                -32000,
                f"mcp server {server_name!r} is not registered",
                request_id,
            )

        # Rewrite the envelope so the downstream proxy sees the
        # un-namespaced tool name (what the upstream actually executes).
        new_envelope = dict(envelope)
        new_params = dict(params) if isinstance(params, dict) else {}
        new_params["name"] = raw_tool
        new_envelope["params"] = new_params
        rewritten_body = json.dumps(new_envelope).encode()

        # Build a fresh ASGI scope around the rewritten body so the
        # downstream proxy reads it via `await request.body()` cleanly.
        request = _request_with_body(request, rewritten_body)
        return await mcp_proxy.handle_mcp_request(server_name, request)

    return mcp_proxy._jsonrpc_error(
        -32601,
        f"aggregator does not implement method {method!r}",
        request_id,
    )


def _request_with_body(original: Request, new_body: bytes) -> Request:
    """Return a new Request whose `body()` yields `new_body`.

    We can't mutate the original ASGI message stream once it's been read,
    so we build a fresh Request around the same scope with a custom
    receive that hands out the new body in one chunk. Headers/auth/etc.
    are preserved untouched — only the body changes.
    """
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": new_body, "more_body": False}

    # Adjust content-length so any downstream code that trusts the header
    # doesn't see a stale value from the original (namespaced) envelope.
    scope = dict(original.scope)
    headers = [(k, v) for k, v in scope.get("headers", []) if k != b"content-length"]
    headers.append((b"content-length", str(len(new_body)).encode()))
    scope["headers"] = headers
    return Request(scope=scope, receive=receive)
