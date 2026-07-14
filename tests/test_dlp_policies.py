"""Tests for the DLP regex Policies feature.

Covers:
  * dlp_policies module — list assembly, hit aggregation, toggle persistence,
    push to dlp-regex, boot_id drift detection
  * dashboard endpoints — admin gating, JSON shape, error surfacing

The httpx call to dlp-regex is stubbed end-to-end; no network leaves the
process. Bundled patterns are replaced with a small synthetic set so
tests don't depend on the real YAML being mounted into the test runner.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx
import pytest

from kyde import auth, dlp_policies, ledger

PASSWORD = "CorrectHorse!Battery9"


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


_FAKE_BUNDLE = {
    "common_regex.email": {
        "id": "common_regex.email",
        "name": "Email Address",
        "source": "common_regex",
        "category": "pii",
        "severity": "LOW",
        "pattern": r"[a-z]+@[a-z]+\.[a-z]+",
        "base_confidence": 0.6,
        "description": "matches an email",
    },
    "gitleaks.aws_key": {
        "id": "gitleaks.aws_key",
        "name": "AWS Access Key",
        "source": "gitleaks",
        "category": "credential",
        "severity": "HIGH",
        "pattern": r"AKIA[0-9A-Z]{16}",
        "base_confidence": 0.9,
    },
    "gitleaks.stripe_key": {
        "id": "gitleaks.stripe_key",
        "name": "Stripe Secret",
        "source": "gitleaks",
        "category": "credential",
        "severity": "HIGH",
        "pattern": r"sk_live_[0-9a-zA-Z]{24}",
        "base_confidence": 0.95,
    },
}


@pytest.fixture(autouse=True)
def _stub_bundle(monkeypatch):
    """Replace the bundled YAML loader with an in-memory fixture set."""
    monkeypatch.setattr(dlp_policies, "_BUNDLED", dict(_FAKE_BUNDLE))
    monkeypatch.setattr(dlp_policies, "_BUNDLED_LOADED", True)
    dlp_policies._set_last_boot_id(None)
    yield
    dlp_policies._set_last_boot_id(None)


class _FakeHttpxClient:
    """Captures POSTs to dlp-regex and replays a canned response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Optional[dict] = None,
        raise_exc: Optional[Exception] = None,
    ):
        self.status_code = status_code
        self.json_body = json_body or {
            "loaded": 0,
            "boot_id": "boot-test-1",
        }
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, *, json=None, **_kwargs):
        self.calls.append({"url": url, "json": json})
        if self.raise_exc is not None:
            raise self.raise_exc
        body = dict(self.json_body)
        body["loaded"] = len((json or {}).get("patterns", []))
        return httpx.Response(
            status_code=self.status_code,
            json=body,
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", url),
        )


def _install_fake_client(monkeypatch, fake: _FakeHttpxClient) -> None:
    monkeypatch.setattr(
        dlp_policies.httpx,
        "AsyncClient",
        lambda *a, **kw: fake,
    )


def _seed_user(username: str, roles: list[str]) -> dict:
    return ledger.create_user(
        username=username,
        email=f"{username}@example.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=roles,
        must_change_password=False,
    )


def _login(client, username: str) -> None:
    resp = client.post(
        "/login",
        data={"username": username, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


@pytest.fixture
def admin_client(client):
    _seed_user("admin", ["admin"])
    _login(client, "admin")
    return client


@pytest.fixture
def viewer_client(client):
    _seed_user("admin", ["admin"])
    _seed_user("viewer", ["viewer"])
    _login(client, "viewer")
    return client


@pytest.fixture
def auditor_client(client):
    _seed_user("admin", ["admin"])
    _seed_user("auditor", ["auditor", "viewer"])
    _login(client, "auditor")
    return client


# ---------------------------------------------------------------------------
# Module-level behaviour
# ---------------------------------------------------------------------------


def test_list_for_ui_marks_disabled_rows():
    dlp_policies._disable("gitleaks.aws_key", user_id=None)
    items = dlp_policies.list_for_ui()
    by_id = {i["id"]: i for i in items}
    assert by_id["gitleaks.aws_key"]["enabled"] is False
    assert by_id["common_regex.email"]["enabled"] is True


def test_active_set_excludes_disabled():
    dlp_policies._disable("gitleaks.aws_key", user_id=None)
    ids = {p["id"] for p in dlp_policies.active_set()}
    assert "gitleaks.aws_key" not in ids
    assert "common_regex.email" in ids


def test_hit_counts_aggregate_from_dlp_alerts():
    """Each finding counts as one hit, even when several share an alert."""
    from psycopg.types.json import Jsonb

    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dlp_alerts (
                    alert_id, entry_id, session_id, scanner, score,
                    findings, dedup_hash, status, seen_count,
                    last_seen_at, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    "alert-1",
                    "entry-1",
                    "sess-1",
                    "regex",
                    0.9,
                    Jsonb(
                        [
                            {"pattern_id": "gitleaks.aws_key", "confidence": 0.9},
                            {"pattern_id": "gitleaks.aws_key", "confidence": 0.92},
                            {"pattern_id": "common_regex.email", "confidence": 0.5},
                        ]
                    ),
                    "hash-1",
                    "new",
                    1,
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()

    items = {i["id"]: i for i in dlp_policies.list_for_ui()}
    assert items["gitleaks.aws_key"]["hits"] == 2
    assert items["common_regex.email"]["hits"] == 1
    assert items["gitleaks.stripe_key"]["hits"] == 0


def test_push_active_set_sends_full_payload(monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)

    body = asyncio.run(dlp_policies.push_active_set())
    assert body["loaded"] == len(_FAKE_BUNDLE)
    assert body["boot_id"] == "boot-test-1"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/v1/patterns/replace")
    sent_ids = {p["id"] for p in fake.calls[0]["json"]["patterns"]}
    assert sent_ids == set(_FAKE_BUNDLE)


def test_set_enabled_writes_row_and_pushes_minus_disabled(monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)

    row = asyncio.run(
        dlp_policies.set_enabled("gitleaks.aws_key", enabled=False, user_id=None)
    )
    assert row["enabled"] is False
    assert "gitleaks.aws_key" in dlp_policies.disabled_ids()
    assert len(fake.calls) == 1
    sent_ids = {p["id"] for p in fake.calls[0]["json"]["patterns"]}
    assert "gitleaks.aws_key" not in sent_ids
    assert "common_regex.email" in sent_ids


def test_set_enabled_re_enable_clears_row(monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)

    asyncio.run(
        dlp_policies.set_enabled("gitleaks.aws_key", enabled=False, user_id=None)
    )
    fake.calls.clear()
    row = asyncio.run(
        dlp_policies.set_enabled("gitleaks.aws_key", enabled=True, user_id=None)
    )
    assert row["enabled"] is True
    assert "gitleaks.aws_key" not in dlp_policies.disabled_ids()
    sent_ids = {p["id"] for p in fake.calls[0]["json"]["patterns"]}
    assert "gitleaks.aws_key" in sent_ids


def test_set_enabled_rejects_unknown_pattern():
    with pytest.raises(ValueError):
        asyncio.run(
            dlp_policies.set_enabled("does.not.exist", enabled=False, user_id=None)
        )


def test_observe_boot_id_seeds_then_triggers_repush(monkeypatch):
    """First observation seeds; subsequent change schedules a re-push."""
    pushes: list[bool] = []

    async def fake_push():
        pushes.append(True)
        return {"loaded": 0, "boot_id": "boot-2"}

    monkeypatch.setattr(dlp_policies, "push_active_set", fake_push)

    dlp_policies.observe_boot_id("boot-1")
    assert pushes == []  # seed only

    dlp_policies.observe_boot_id("boot-1")
    assert pushes == []  # same id

    dlp_policies.observe_boot_id("boot-2")
    # observe_boot_id runs the task with asyncio.run when no loop is
    # active, so the push has already completed by the time we get here.
    assert pushes == [True]


def test_request_recovery_push_debounces(monkeypatch):
    """503 burst from dlp-regex should not spawn one push per scan.

    The debounce is only observable when calls happen inside a running
    loop (the production case — fastapi's request handler). When there's
    no loop, each call runs the push synchronously to completion, which
    is fine: the cost we're avoiding is concurrent in-flight pushes.
    """
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    monkeypatch.setattr(dlp_policies, "_RECOVERY_IN_FLIGHT", False)

    async def burst():
        for _ in range(5):
            dlp_policies.request_recovery_push()
        # Yield to the loop so the single scheduled task can finish.
        await asyncio.sleep(0)
        # Drain any pending tasks created by the burst.
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(burst())
    assert len(fake.calls) == 1


def test_push_with_retries_succeeds_after_transient_failure(monkeypatch):
    """Startup push retries on transient failure and eventually succeeds."""
    attempts = {"n": 0}

    class _FlakyClient(_FakeHttpxClient):
        async def post(self, url, *, json=None, **_kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("not ready yet")
            return await super().post(url, json=json, **_kwargs)

    fake = _FlakyClient()
    _install_fake_client(monkeypatch, fake)
    monkeypatch.setattr(dlp_policies, "_STARTUP_BACKOFF_S", 0)

    body = asyncio.run(dlp_policies.push_active_set_with_retries())
    assert body is not None
    assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------


def test_list_rejects_viewer(viewer_client):
    """A plain viewer (no admin, no auditor) cannot see policies."""
    resp = viewer_client.get("/api/dlp-policies")
    assert resp.status_code == 403


def test_list_allows_auditor(auditor_client):
    """Auditors triage FPs, so they need to read the policy list too."""
    resp = auditor_client.get("/api/dlp-policies")
    assert resp.status_code == 200
    assert {i["id"] for i in resp.json()["items"]} == set(_FAKE_BUNDLE)


def test_list_returns_items(admin_client):
    resp = admin_client.get("/api/dlp-policies")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    ids = {i["id"] for i in body["items"]}
    assert ids == set(_FAKE_BUNDLE)


def test_patch_toggles_and_pushes(admin_client, monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)

    resp = admin_client.patch(
        "/api/dlp-policies/gitleaks.aws_key",
        json={"enabled": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    assert "gitleaks.aws_key" in dlp_policies.disabled_ids()
    assert len(fake.calls) == 1


def test_patch_rejects_viewer(viewer_client):
    resp = viewer_client.patch(
        "/api/dlp-policies/gitleaks.aws_key", json={"enabled": False}
    )
    assert resp.status_code == 403


def test_patch_rejects_auditor(auditor_client, monkeypatch):
    """Policies are read-only for auditors: toggling a pattern is a config
    change, reserved for admins. Auditors disposition individual alerts via
    the alert-transition endpoint instead."""
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    resp = auditor_client.patch(
        "/api/dlp-policies/gitleaks.aws_key", json={"enabled": False}
    )
    assert resp.status_code == 403
    # No write and no push happened.
    assert "gitleaks.aws_key" not in dlp_policies.disabled_ids()
    assert len(fake.calls) == 0


def test_patch_rejects_unknown_pattern(admin_client, monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    resp = admin_client.patch(
        "/api/dlp-policies/does.not.exist", json={"enabled": False}
    )
    assert resp.status_code == 404


def test_patch_rejects_non_boolean(admin_client, monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    resp = admin_client.patch(
        "/api/dlp-policies/gitleaks.aws_key", json={"enabled": "yes"}
    )
    assert resp.status_code == 400


def test_patch_surfaces_push_failure_as_502(admin_client, monkeypatch):
    fake = _FakeHttpxClient(raise_exc=httpx.ConnectError("nope"))
    _install_fake_client(monkeypatch, fake)

    resp = admin_client.patch(
        "/api/dlp-policies/gitleaks.aws_key", json={"enabled": False}
    )
    assert resp.status_code == 502
    # The DB write still happened — the toggle is sticky, the push isn't.
    assert "gitleaks.aws_key" in dlp_policies.disabled_ids()


def test_resync_issues_one_push(admin_client, monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)

    resp = admin_client.post("/api/dlp-policies/resync")
    assert resp.status_code == 200
    assert resp.json()["boot_id"] == "boot-test-1"
    assert len(fake.calls) == 1


def test_resync_rejects_viewer(viewer_client):
    resp = viewer_client.post("/api/dlp-policies/resync")
    assert resp.status_code == 403


def test_resync_rejects_auditor(auditor_client, monkeypatch):
    """Resync re-pushes the active set — an admin-only write."""
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    resp = auditor_client.post("/api/dlp-policies/resync")
    assert resp.status_code == 403
    assert len(fake.calls) == 0


# ---------------------------------------------------------------------------
# Prevention store (per-pattern blocking opt-in)
# ---------------------------------------------------------------------------


def test_prevention_defaults_off_and_round_trips():
    assert dlp_policies.prevention_ids() == set()

    row = dlp_policies.set_prevention("gitleaks.aws_key", True, user_id=None)
    assert row["prevention"] is True
    assert dlp_policies.prevention_ids() == {"gitleaks.aws_key"}

    row = dlp_policies.set_prevention("gitleaks.aws_key", False, user_id=None)
    assert row["prevention"] is False
    assert dlp_policies.prevention_ids() == set()


def test_prevention_rejects_unknown_pattern():
    with pytest.raises(ValueError):
        dlp_policies.set_prevention("nope.unknown", True, user_id=None)


def test_prevention_does_not_push_to_dlp_regex(monkeypatch):
    fake = _FakeHttpxClient()
    _install_fake_client(monkeypatch, fake)
    dlp_policies.set_prevention("gitleaks.aws_key", True, user_id=None)
    assert fake.calls == []


def test_prevention_bulk_enable_and_disable():
    result = dlp_policies.set_prevention_bulk(True, user_id=None)
    assert result["updated"] == len(_FAKE_BUNDLE)
    assert dlp_policies.prevention_ids() == set(_FAKE_BUNDLE)

    result = dlp_policies.set_prevention_bulk(False, user_id=None)
    assert result["updated"] == len(_FAKE_BUNDLE)
    assert dlp_policies.prevention_ids() == set()


def test_list_for_ui_includes_prevention_flag():
    dlp_policies.set_prevention("gitleaks.aws_key", True, user_id=None)
    items = {i["id"]: i for i in dlp_policies.list_for_ui()}
    assert items["gitleaks.aws_key"]["prevention"] is True
    assert items["common_regex.email"]["prevention"] is False


# NOTE: test_patch_prevention_endpoint moved to the kyde-enterprise repo —
# the prevention write-path returns 404 without the enterprise enforce package.


def test_patch_requires_enabled_or_prevention(admin_client):
    resp = admin_client.patch("/api/dlp-policies/gitleaks.aws_key", json={})
    assert resp.status_code == 400


# NOTE: test_prevention_bulk_endpoint moved to the kyde-enterprise repo (enterprise
# enforcement write-path). The viewer-rejection case below stays — RBAC is core.


def test_prevention_bulk_rejects_viewer(viewer_client):
    resp = viewer_client.post(
        "/api/dlp-policies/prevention-bulk", json={"enabled": True}
    )
    assert resp.status_code == 403
