"""Starter-edition tests for the incremental + TTL-cached verify_chain.

The ledger is append-only and chain-linked, so once entries 1..N are
verified clean they cannot become invalid. verify_chain caches the
high-water mark and the per-call work shrinks to "verify only new rows
since last call".

The signing-aware cases (spying on verify_payload, signature-tamper
detection) live in the private kyde-enterprise repo — they require the enterprise
``kyde.signing`` package. What remains here exercises the cache bookkeeping
that runs in every edition.
"""

from __future__ import annotations

from kyde import ledger
from kyde.testing import append_simple as _append_simple


# ---------------------------------------------------------------------------
# Cold call populates the cache
# ---------------------------------------------------------------------------


def test_cold_call_populates_cache():
    for i in range(3):
        _append_simple(f"agent:{i}")

    assert ledger._VERIFY_CACHE is None
    valid, errors = ledger.verify_chain(record=False)
    assert valid
    assert errors == []

    cached = ledger._VERIFY_CACHE
    assert cached is not None
    assert cached.total == 3
    assert cached.last_seq == 3
    assert cached.valid is True


# ---------------------------------------------------------------------------
# Audit trail still written on cache hit (record=True path)
# ---------------------------------------------------------------------------


def test_record_true_writes_verification_run_on_cache_hit():
    """The cache must not suppress verification_runs inserts — Compliance
    page and _sync_chain_incidents both depend on every call producing
    an audit row."""
    for i in range(2):
        _append_simple(f"agent:{i}")

    ledger.verify_chain(record=True)  # cold, 1 row
    runs_after_cold = ledger.list_verification_runs(limit=10)

    ledger.verify_chain(record=True)  # warm cache hit
    runs_after_warm = ledger.list_verification_runs(limit=10)

    assert len(runs_after_warm) == len(runs_after_cold) + 1
