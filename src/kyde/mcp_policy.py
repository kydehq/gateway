"""
Per-tool policy lookup with most-specific-wins precedence.

`mcp_tool_policies` stores (server_id, agent_id, tool_name) → (decision, reason)
rows. `*` is a literal wildcard in both `agent_id` and `tool_name`, so the
tenant-wide default for a server is `('*', '*')`. Lookup precedence
(declared in docs/plans/mcp-routing-v1.md and the 0013 migration):

    (server, agent, tool)  >  (server, *, tool)  >  (server, agent, *)
                           >  (server, *, *)
    No row matches → default allow.

Cache mirrors mcp_registry: per-process, 5 s TTL keyed on server_id. Writes
in the same process invalidate immediately so the dashboard feels live;
cross-process readers pick up the change inside the TTL window. The proxy
hot path therefore costs at most one Postgres round-trip per server per
five seconds.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Literal, Optional

from . import ledger

Decision = Literal["allow", "deny"]

_CACHE_TTL = 5.0  # seconds
# key = server_id (string); value = (expires_at_monotonic, [policy rows])
_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = Lock()


def invalidate_cache(server_id: Optional[str] = None) -> None:
    """Drop cached rows for a single server, or everything when server_id is None."""
    with _cache_lock:
        if server_id is None:
            _cache.clear()
            return
        _cache.pop(str(server_id), None)


def _load(server_id: str) -> list[dict]:
    now = time.monotonic()
    key = str(server_id)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    rows = ledger.list_mcp_tool_policies(key)
    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL, rows)
    return rows


def list_policies(server_id: str) -> list[dict]:
    """Return all policy rows for a server (used by the dashboard API)."""
    return list(_load(server_id))


def upsert_policy(
    server_id: str,
    agent_id: str,
    tool_name: str,
    decision: Decision,
    reason: Optional[str],
    user_id: Optional[int],
) -> dict:
    """Insert-or-update one (server, agent, tool) row and invalidate the cache."""
    if decision not in ("allow", "deny"):
        raise ValueError("decision must be 'allow' or 'deny'")
    row = ledger.upsert_mcp_tool_policy(
        server_id, agent_id, tool_name, decision, reason, user_id
    )
    invalidate_cache(server_id)
    return row


def delete_policy(server_id: str, agent_id: str, tool_name: str) -> bool:
    """Remove one row; returns True iff a row was deleted."""
    deleted = ledger.delete_mcp_tool_policy(server_id, agent_id, tool_name)
    if deleted:
        invalidate_cache(server_id)
    return deleted


def check_policy(
    server_id: str, agent_id: str, tool_name: str
) -> tuple[Decision, Optional[str]]:
    """Most-specific-wins lookup. Default-allow when no row matches.

    Precedence (highest first):
        (server, agent, tool)
        (server, *,     tool)
        (server, agent, *)
        (server, *,     *)
    """
    rows = _load(server_id)
    # Bucket once so we can fall through the precedence ladder without
    # rescanning the list four times. Server_id is already pinned by
    # _load — every row in `rows` matches this server.
    exact: Optional[dict] = None
    any_agent_tool: Optional[dict] = None
    agent_any_tool: Optional[dict] = None
    fallback: Optional[dict] = None
    for r in rows:
        rid, rtool = r["agent_id"], r["tool_name"]
        if rid == agent_id and rtool == tool_name:
            exact = r
        elif rid == "*" and rtool == tool_name:
            any_agent_tool = r
        elif rid == agent_id and rtool == "*":
            agent_any_tool = r
        elif rid == "*" and rtool == "*":
            fallback = r

    chosen = exact or any_agent_tool or agent_any_tool or fallback
    if chosen is None:
        return ("allow", None)
    return (chosen["decision"], chosen.get("reason"))
