"""
Per-tenant MCP server registry — cached resolver on top of ledger CRUD.

The registry is a pure routing table. There is no credential storage: the
gateway forwards the agent's Authorization header to the upstream
unchanged (credential handling is deliberately out of scope).

The cache mirrors settings.py: short TTL (5 s) so the proxy hot path
doesn't hit Postgres on every request, but writes invalidate immediately
so the dashboard feels live. Lookup keys are (tenant_id, name) for the
single-server path and tenant_id for the list path.

Until the hybrid-SaaS rollout lands per-tenant identity, all reads/writes
use DEFAULT_TENANT — the schema column is present so we don't need a
backfill migration later.
"""

from __future__ import annotations

import re
import time
from threading import Lock
from typing import Optional

from . import ledger

DEFAULT_TENANT = "default"

_CACHE_TTL = 5.0  # seconds
_cache_servers: dict[tuple[str, str], tuple[float, Optional[dict]]] = {}
_cache_lists: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = Lock()

# A server "name" is what shows up in the routing path /mcp/{name}. Keep
# it URL-safe and small enough that operators can type it from memory.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            "server name must be 1-63 chars, lowercase alphanumerics, "
            "hyphens or underscores, starting with a letter or digit"
        )


def _validate_url(url: str) -> None:
    # Minimal: scheme must be http/https and host non-empty. Full URL
    # parsing happens at proxy time; this catches operator typos at
    # registration so they don't surface as runtime confusion.
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("upstream_url must start with http:// or https://")
    if len(url) < len("https://x"):
        raise ValueError("upstream_url is too short to be valid")


def invalidate_cache(tenant_id: Optional[str] = None) -> None:
    """Drop cached entries for one tenant (or everything when tenant_id is None)."""
    with _cache_lock:
        if tenant_id is None:
            _cache_servers.clear()
            _cache_lists.clear()
            return
        _cache_lists.pop(tenant_id, None)
        for key in list(_cache_servers):
            if key[0] == tenant_id:
                _cache_servers.pop(key, None)


def list_servers(tenant_id: str = DEFAULT_TENANT) -> list[dict]:
    """Cached list of all servers for a tenant."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache_lists.get(tenant_id)
        if hit and hit[0] > now:
            return hit[1]
    rows = ledger.list_mcp_servers(tenant_id)
    with _cache_lock:
        _cache_lists[tenant_id] = (now + _CACHE_TTL, rows)
    return rows


def get_server(name: str, tenant_id: str = DEFAULT_TENANT) -> Optional[dict]:
    """Cached single-server lookup. Returns None when no row exists."""
    now = time.monotonic()
    key = (tenant_id, name)
    with _cache_lock:
        hit = _cache_servers.get(key)
        if hit and hit[0] > now:
            return hit[1]
    row = ledger.get_mcp_server(tenant_id, name)
    with _cache_lock:
        _cache_servers[key] = (now + _CACHE_TTL, row)
    return row


def upsert_server(
    name: str,
    upstream_url: str,
    *,
    enabled: bool = True,
    tenant_id: str = DEFAULT_TENANT,
    user_id: Optional[int] = None,
) -> dict:
    """Register-or-update a server. Validates inputs before touching the DB."""
    _validate_name(name)
    _validate_url(upstream_url)
    row = ledger.upsert_mcp_server(tenant_id, name, upstream_url, enabled, user_id)
    invalidate_cache(tenant_id)
    return row


def delete_server(name: str, tenant_id: str = DEFAULT_TENANT) -> bool:
    """Remove a server. Returns True iff a row was deleted."""
    deleted = ledger.delete_mcp_server(tenant_id, name)
    if deleted:
        invalidate_cache(tenant_id)
    return deleted
