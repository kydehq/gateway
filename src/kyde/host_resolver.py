"""
Reverse-DNS resolver with persistent caching in `host_resolutions`.

Synchronous-with-short-timeout: when an unknown or expired IP is read, we
fire a single PTR lookup inline with a runtime-configured timeout, then
persist the result (hit or miss). Subsequent reads are O(1) DB lookups
until the TTL expires.

Why not background-resolve at write time? PTR latency is environment-
dependent (can stretch into seconds on enterprise networks with slow
recursors) and the proxy hot path is the wrong place to absorb that.
The lazy-on-read pattern means the cost is paid by whoever first opens
a Network Map / Host page for a never-before-seen IP — typically an
admin, who is the same person who can also raise the timeout if needed.

Admin labels (source='admin') are never overwritten by DNS refresh —
the gate lives in `upsert_host_dns`.

Tests mock `_reverse_dns_sync` directly so CI never hits the real
system resolver.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import socket
from dataclasses import dataclass
from typing import Optional

from . import ledger, settings as settings_module

log = logging.getLogger(__name__)


@dataclass
class HostResolution:
    ip: str
    hostname: Optional[str]
    source: str  # 'admin' | 'dns'
    resolved_at_iso: str  # ISO 8601 UTC
    ttl_seconds: int


def _reverse_dns_sync(ip: str) -> Optional[str]:
    """Blocking PTR lookup. Returns None on any failure — NXDOMAIN, a
    malformed IP, the resolver being unreachable, anything. Separated as
    its own function so tests can monkeypatch it without touching the
    event loop machinery."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
    except Exception:
        return None
    return hostname or None


async def reverse_dns(ip: str, *, timeout: float | None = None) -> Optional[str]:
    """Async wrapper around _reverse_dns_sync with a timeout enforced via
    asyncio.wait_for. None on timeout or any underlying failure."""
    if not ip:
        return None
    if timeout is None:
        timeout = float(settings_module.get("HOST_DNS_TIMEOUT_SECONDS"))
    loop = asyncio.get_running_loop()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(pool, _reverse_dns_sync, ip),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, Exception):
            return None
    finally:
        # Shut down the executor without waiting for the lookup to drain —
        # we already gave up on it via the timeout.
        pool.shutdown(wait=False, cancel_futures=True)


def _cached_value_or_none(row: Optional[dict]) -> Optional[HostResolution]:
    """Decide whether a host_resolutions row is fresh enough to use as-is.

    Admin rows are always fresh (no TTL). DNS rows are fresh when
    resolved_at + ttl_seconds is still in the future.
    """
    if row is None:
        return None
    if row["source"] == "admin":
        return _to_resolution(row)
    # row['resolved_at'] is a timezone-aware datetime; row['ttl_seconds'] is int.
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    expires = row["resolved_at"] + timedelta(seconds=int(row["ttl_seconds"]))
    if now < expires:
        return _to_resolution(row)
    return None


def _to_resolution(row: dict) -> HostResolution:
    return HostResolution(
        ip=row["ip"],
        hostname=row["hostname"],
        source=row["source"],
        resolved_at_iso=row["resolved_at"].isoformat(),
        ttl_seconds=int(row["ttl_seconds"]),
    )


async def resolve_and_cache(ip: str, *, force: bool = False) -> HostResolution:
    """The hot path. Look up host_resolutions for `ip`:
      - If a fresh row exists (admin always, dns within TTL): return it.
      - Otherwise, run reverse_dns and persist as a dns row.

    `force=True` bypasses the freshness check — used by the admin Refresh
    button. Force still respects the admin-precedence rule: if an admin
    row exists, return it untouched (admin labels shouldn't be flushed by
    a refresh click that's intended for stale dns entries)."""
    row = ledger.get_host_resolution(ip)
    if row and row["source"] == "admin":
        return _to_resolution(row)
    if not force:
        cached = _cached_value_or_none(row)
        if cached is not None:
            return cached

    hostname = await reverse_dns(ip)
    ttl = int(
        settings_module.get(
            "HOST_DNS_TTL_HIT_SECONDS" if hostname else "HOST_DNS_TTL_MISS_SECONDS"
        )
    )
    ledger.upsert_host_dns(ip=ip, hostname=hostname, ttl_seconds=ttl)
    fresh = ledger.get_host_resolution(ip)
    assert fresh is not None
    return _to_resolution(fresh)


def get_cached(ip: str) -> Optional[HostResolution]:
    """Read-only cache lookup (no DNS call, no TTL check). Used by
    decoration code paths that want to enrich a response with a hostname
    when one is known, without paying the resolver cost themselves.

    Returns None if absent OR if it's a stale dns row — callers shouldn't
    show stale dns hostnames; if they want fresh data, use
    resolve_and_cache."""
    row = ledger.get_host_resolution(ip)
    return _cached_value_or_none(row)
