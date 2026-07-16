"""Shared pytest support for the gateway test suites.

This module is shipped by ``kyde-gateway`` so that BOTH the public core repo
and the private ``kyde-enterprise`` repo build their suites on one source of
truth — the Postgres bootstrap, the per-test ``clean_db`` truncation, the
signing-key redirect, and the common fixtures/helpers. Previously each repo
kept a hand-copied ``conftest.py`` (the enterprise copy literally carried a
"consider extracting a shared pytest plugin" note); the TRUNCATE list drifted
between them. Now there is exactly one.

Usage — each repo's ``tests/conftest.py`` does::

    from kyde.testing import bootstrap
    bootstrap()
    from kyde.testing import clean_db, client, strong_password  # noqa: F401

It is a plain importable module rather than a ``pytest11`` entry-point plugin
on purpose: the bootstrap connects to Postgres and the ``clean_db`` autouse
fixture truncates tables, so it must be opt-in per repo, never auto-activated
in some unrelated project that merely has ``kyde-gateway`` installed.

All ``kyde.*`` imports are deferred into the functions/fixtures so that simply
importing this module never pulls in ``ledger`` (and reads config) before
``bootstrap()`` has set ``DATABASE_URL``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import psycopg
import pytest

# Resolve the Postgres connection up front; ledger.py reads DATABASE_URL on
# first use. Overridable for CI via TEST_POSTGRES_URL.
_PG_BASE = os.environ.get(
    "TEST_POSTGRES_URL",
    "postgresql://witness:witness-dev-only@localhost:5432",
)
_TEST_DB = "witness_test"
TEST_DATABASE_URL = f"{_PG_BASE}/{_TEST_DB}"

# A password that satisfies auth.validate_password for seeded test users.
PASSWORD = "CorrectHorse!Battery9"

# Signing keys are redirected here so tests never touch ~/.agent-ledger/.
_KEY_TMPDIR = Path(tempfile.mkdtemp(prefix="kyde-test-keys-"))

# Every table the suite mutates. Centralised here so the two repos can never
# drift — add new tables in ONE place.
_TRUNCATE_TABLES = (
    "ledger, dlp_alert_events, dlp_alerts, "
    "dlp_disabled_patterns, dlp_prevention_patterns, dlp_rules, "
    "session_turns, session_intents, sessions, agents, "
    "agent_blocks, verification_runs, host_resolutions, "
    "agent_traffic_meters, agent_traffic_mode_history, "
    "mcp_tool_policies, mcp_servers, admin_actions, "
    "auth_sessions, users"
)


def _ensure_test_db() -> None:
    """Create the dedicated test database if it doesn't exist yet."""
    with psycopg.connect(f"{_PG_BASE}/postgres", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_TEST_DB}"')


def _setup_signing_keys() -> None:
    from kyde import signing

    signing.KEY_DIR = _KEY_TMPDIR
    signing.PRIVATE_KEY_PATH = _KEY_TMPDIR / "signing.key"
    signing.PUBLIC_KEY_PATH = _KEY_TMPDIR / "signing.pub"
    signing.TPM_KEY_PATH = _KEY_TMPDIR / "tpm_key.pem"
    # Force software signing — the host may have a real TPM that the probe
    # latched to at import time. Tests shouldn't exercise the TPM path.
    signing._TPM_AVAILABLE = False
    signing.generate_keypair()


def bootstrap() -> None:
    """Prepare the process for a test run: ensure the test DB exists, point
    ``DATABASE_URL`` at it, and redirect signing keys.

    Call this at the very top of ``tests/conftest.py``, before any ``kyde``
    submodule is imported for use. Signing-key setup only runs when the enterprise
    ``kyde.signing`` package is present (enterprise edition / enterprise repo); the
    starter core build runs unsigned and skips it.
    """
    _ensure_test_db()
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from kyde._features import HAS_SIGNING

    if HAS_SIGNING:
        _setup_signing_keys()


# ---------------------------------------------------------------------------
# Helper functions (importable directly: `from kyde.testing import ...`)
# ---------------------------------------------------------------------------


def append_simple(agent_id: str = "agent:test", **overrides: Any):
    """Append one minimal chat row to the ledger and return the entry."""
    from kyde import ledger

    defaults = dict(
        agent_id=agent_id,
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
    )
    defaults.update(overrides)
    return ledger.append(**defaults)


def chat(
    agent_id: str,
    *,
    action_type: str = "chat",
    request_kind: str = "chat",
    prompt: int = 100,
    completion: int = 50,
    model: str = "gpt-4o-mini",
) -> None:
    """Append a chat row with token counts — for trust/economics tests."""
    from kyde import ledger

    ledger.append(
        agent_id=agent_id,
        action_type=action_type,
        model=model,
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "hello"}}]},
        why_messages=[{"role": "user", "content": "hi"}],
        tool_calls=[],
        prompt_tokens=prompt,
        completion_tokens=completion,
        request_kind=request_kind,
    )


def seed_user(username: str, roles: list[str], password: str = PASSWORD) -> dict:
    """Create a user with the given roles (no forced password change)."""
    from kyde import auth, ledger

    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(password),
        roles=roles,
        must_change_password=False,
    )


def login(client, username: str, password: str = PASSWORD) -> None:
    """Log a seeded user into the dashboard TestClient (303 on success)."""
    resp = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


# ---------------------------------------------------------------------------
# Fixtures (re-export the ones you need from each repo's conftest)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_db():
    """Truncate all mutable tables and clear in-process caches between tests."""
    from kyde import ledger

    # Force schema init by touching the pool before truncating.
    ledger._get_pool()

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE")
        conn.commit()

    ledger._reset_verify_cache()
    # Settings rows aren't truncated (the table doubles as deployment config),
    # but the in-process cache must not leak a previous test's override (e.g. a
    # prevention toggle) into the next test.
    from kyde import settings as _settings

    _settings.invalidate_cache()
    yield


@pytest.fixture
def client():
    """FastAPI TestClient for the dashboard app."""
    from fastapi.testclient import TestClient

    from kyde import dashboard

    return TestClient(dashboard.app)


@pytest.fixture
def strong_password() -> str:
    """A password that satisfies auth.validate_password for test users."""
    return PASSWORD


@pytest.fixture
def admin_client(client):
    """A logged-in admin TestClient."""
    seed_user("admin", ["admin"])
    login(client, "admin")
    return client


@pytest.fixture
def viewer_client(client):
    """A logged-in viewer TestClient (admin also seeded for the bootstrap gate)."""
    seed_user("admin", ["admin"])
    seed_user("viewer", ["viewer"])
    login(client, "viewer")
    return client


@pytest.fixture
def auditor_client(client):
    """A logged-in auditor TestClient (admin also seeded for the bootstrap gate)."""
    seed_user("admin", ["admin"])
    seed_user("auditor", ["auditor", "viewer"])
    login(client, "auditor")
    return client
