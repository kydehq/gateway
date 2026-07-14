"""Pytest bootstrap for the gateway (core) test suite.

The actual fixtures and helpers live in ``kyde.testing`` — a module shipped by
``kyde-gateway`` and shared with the private ``kyde-enterprise`` repo so the
Postgres bootstrap, per-test truncation, and signing-key redirect have a single
source of truth (no more hand-copied conftests drifting apart).

``bootstrap()`` runs before any ``kyde`` submodule is imported for use, because
ledger.py reads ``DATABASE_URL`` on first touch. Overridable via
``TEST_POSTGRES_URL`` for CI.
"""

from kyde.testing import bootstrap

bootstrap()

# Re-export the fixtures this suite uses so pytest discovers them at the root.
from kyde.testing import (  # noqa: E402,F401
    clean_db,
    client,
    strong_password,
)
