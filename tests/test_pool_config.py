"""Env-overridable pool sizing — _pool_min / _pool_max read from
KYDE_DB_POOL_MIN / KYDE_DB_POOL_MAX with sensible defaults.
Operators tune these without code changes: the connection pool,
not the UPSERT, is the first throughput ceiling under load.
"""

from __future__ import annotations


from kyde import ledger


def test_pool_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("KYDE_DB_POOL_MIN", raising=False)
    monkeypatch.delenv("KYDE_DB_POOL_MAX", raising=False)
    assert ledger._pool_min() == ledger._DEFAULT_POOL_MIN
    assert ledger._pool_max() == ledger._DEFAULT_POOL_MAX


def test_pool_min_overridable(monkeypatch):
    monkeypatch.setenv("KYDE_DB_POOL_MIN", "5")
    assert ledger._pool_min() == 5


def test_pool_max_overridable(monkeypatch):
    monkeypatch.setenv("KYDE_DB_POOL_MAX", "50")
    assert ledger._pool_max() == 50


def test_pool_max_floors_to_min(monkeypatch):
    # max < min is nonsensical; clamp max up to min so the pool ctor
    # doesn't raise.
    monkeypatch.setenv("KYDE_DB_POOL_MIN", "8")
    monkeypatch.setenv("KYDE_DB_POOL_MAX", "3")
    assert ledger._pool_min() == 8
    assert ledger._pool_max() == 8


def test_pool_min_floors_to_one(monkeypatch):
    monkeypatch.setenv("KYDE_DB_POOL_MIN", "0")
    assert ledger._pool_min() == 1


def test_pool_garbage_falls_back_to_default(monkeypatch):
    # A typo in the env var shouldn't break startup — falling back to
    # the default is friendlier than failing fast here.
    monkeypatch.setenv("KYDE_DB_POOL_MIN", "not-a-number")
    monkeypatch.setenv("KYDE_DB_POOL_MAX", "also-not")
    assert ledger._pool_min() == ledger._DEFAULT_POOL_MIN
    assert ledger._pool_max() == ledger._DEFAULT_POOL_MAX
