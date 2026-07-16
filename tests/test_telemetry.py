"""
Tests for the telemetry emitter (kyde.telemetry).

Covers the two things that matter most for a compliance product: the payload
carries only aggregates (never raw content or identifiers), and the delta
watermark advances only on a successful send.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import time

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from kyde import ledger, settings, telemetry
from kyde.testing import chat


def _seed_dlp_alert(
    pattern_id: str, category: str, entity_type: str, secret: str, score: float
) -> None:
    """One ledger entry + one DLP alert whose findings carry derived metadata
    plus a raw matched_value that must never appear in telemetry."""
    chat("agent:dlp")
    findings = json.dumps(
        [
            {
                "pattern_id": pattern_id,
                "category": category,
                "entity_type": entity_type,
                "matched_value": secret,
                "confidence": score,
            }
        ]
    )
    now = time.time()
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT entry_id FROM ledger ORDER BY seq DESC LIMIT 1")
            entry_id = cur.fetchone()["entry_id"]
            cur.execute(
                "INSERT INTO dlp_alerts (alert_id, entry_id, scanner, findings, "
                "score, status, severity, dedup_hash, created_at, updated_at) "
                "VALUES ('t2', %s, 'regex', %s::jsonb, %s, 'new', 'high', 't2', %s, %s)",
                (entry_id, findings, score, now, now),
            )
        conn.commit()


def _reset_watermark(last_sent: float = 0.0) -> None:
    ledger.set_telemetry_last_sent(last_sent, "", "")


# ---------------------------------------------------------------------------
# Payload shape + counts
# ---------------------------------------------------------------------------


def test_build_payload_shape_and_counts():
    chat("agent:a", prompt=100, completion=50)
    chat("agent:a", prompt=10, completion=5)
    chat("agent:b", prompt=1, completion=1)

    batch = telemetry.build_payload(0.0, time.time() + 1)

    env = batch["envelope"]
    assert env["schema_version"] == telemetry.SCHEMA_VERSION
    assert env["batch_kind"] == "delta"
    assert set(("install", "config", "usage", "health")) <= set(batch)

    usage = batch["usage"]
    assert usage["requests_total"] == 3
    assert usage["unique_agents"] == 2
    assert usage["by_action_type"]["chat"] == 3
    assert usage["tokens"]["prompt"] == 111
    assert usage["tokens"]["completion"] == 56
    assert usage["tokens"]["total"] == 167


def test_config_section_reports_counts_and_sources():
    batch = telemetry.build_payload(0.0, time.time() + 1)
    config = batch["config"]
    # Adoption counts are present…
    assert "counts" in config and "users" in config["counts"]
    # …and the telemetry flag itself is reported with its resolution source.
    assert config["TELEMETRY_ENABLED"]["value"] is False
    assert config["TELEMETRY_ENABLED"]["source"] in {"db", "env", "default"}
    # A sensitive key collapses to presence-only, never a value.
    assert set(config["TELEMETRY_ENDPOINT"]) == {"is_set", "source"}


# ---------------------------------------------------------------------------
# Privacy guarantees
# ---------------------------------------------------------------------------


def test_no_raw_content_or_identifiers_leak():
    secret = "SUPER-SECRET-PROMPT-4242"
    chat("agent:leaky")
    # A DLP alert whose findings carry a matched value that must never ship.
    entry = ledger._conn
    with entry() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT entry_id FROM ledger ORDER BY seq DESC LIMIT 1")
            entry_id = cur.fetchone()["entry_id"]
            findings = json.dumps(
                [{"category": "pii", "matched_value": secret, "pattern_id": "ssn"}]
            )
            now = time.time()
            cur.execute(
                "INSERT INTO dlp_alerts (alert_id, entry_id, scanner, findings, "
                "score, status, severity, dedup_hash, created_at, updated_at) "
                "VALUES ('a1', %s, 'regex', %s::jsonb, 0.9, 'new', 'high', 'a1', %s, %s)",
                (entry_id, findings, now, now),
            )
        conn.commit()

    blob = json.dumps(telemetry.build_payload(0.0, time.time() + 1))

    # No raw content, no matched secret, no raw hostname.
    assert secret not in blob
    assert socket.gethostname() not in blob
    # None of the never-send field names appear anywhere in the payload.
    for forbidden in ("full_messages", "why", "findings", "client_ip", "matched_value"):
        assert forbidden not in blob
    # But the DLP alert IS counted (label only).
    usage = telemetry.build_payload(0.0, time.time() + 1)["usage"]
    assert usage["dlp_alerts_by_severity"].get("high") == 1
    assert usage["dlp_alerts_by_scanner"].get("regex") == 1


def test_gateway_id_is_hmac_pseudonym():
    batch = telemetry.build_payload(0.0, time.time() + 1)
    gid = batch["envelope"]["gateway_id"]
    assert gid == telemetry._pseudonym(socket.gethostname())
    assert gid != socket.gethostname()
    assert len(gid) == 64 and all(c in "0123456789abcdef" for c in gid)


def test_window_is_hour_floored():
    batch = telemetry.build_payload(12_345.6, 98_765.4)
    env = batch["envelope"]
    assert env["window_start"] % 3600 == 0
    assert env["window_end"] % 3600 == 0
    assert env["window_start"] == 12_345 // 3600 * 3600


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_signature_verifies_and_detects_tampering():
    batch = telemetry.build_payload(0.0, time.time() + 1)
    sig = base64.b64decode(batch["signature"])
    unsigned = {k: v for k, v in batch.items() if k != "signature"}

    pub = load_pem_public_key(telemetry._public_pem())
    # Valid signature verifies (raises nothing).
    pub.verify(sig, telemetry._canonical_bytes(unsigned))

    # Tampering with any field breaks verification.
    unsigned["usage"]["requests_total"] = 999_999
    with pytest.raises(InvalidSignature):
        pub.verify(sig, telemetry._canonical_bytes(unsigned))


# ---------------------------------------------------------------------------
# Watermark / delivery
# ---------------------------------------------------------------------------


def test_watermark_advances_only_on_success(monkeypatch):
    _reset_watermark(0.0)
    monkeypatch.setattr(
        telemetry.settings,
        "get",
        lambda k: (
            "https://collector.example/v1/ingest" if k == "TELEMETRY_ENDPOINT" else None
        ),
    )

    async def _ok(endpoint, batch):
        return True, ""

    monkeypatch.setattr(telemetry, "_post_batch", _ok)
    result = asyncio.run(telemetry.emit_once())

    assert result["status"] == "ok"
    state = ledger.get_telemetry_state()
    assert state["last_sent"] > 0
    assert state["last_status"] == "ok"


def test_watermark_holds_on_failure(monkeypatch):
    _reset_watermark(100.0)
    monkeypatch.setattr(
        telemetry.settings,
        "get",
        lambda k: (
            "https://collector.example/v1/ingest" if k == "TELEMETRY_ENDPOINT" else None
        ),
    )

    async def _fail(endpoint, batch):
        return False, "boom"

    monkeypatch.setattr(telemetry, "_post_batch", _fail)
    result = asyncio.run(telemetry.emit_once())

    assert result["status"] == "error"
    state = ledger.get_telemetry_state()
    assert state["last_sent"] == 100.0  # unchanged → same window retried next cycle
    assert state["last_error"] == "boom"


def test_emit_no_endpoint_does_not_advance(monkeypatch):
    _reset_watermark(55.0)
    monkeypatch.setattr(telemetry.settings, "get", lambda k: "")
    result = asyncio.run(telemetry.emit_once())
    assert result["status"] == "no_endpoint"
    assert ledger.get_telemetry_state()["last_sent"] == 55.0


# ---------------------------------------------------------------------------
# Tier 2 (content-derived features) — consent-gated
# ---------------------------------------------------------------------------


def test_tier2_absent_without_consent():
    chat("agent:a")
    batch = telemetry.build_payload(0.0, time.time() + 1)
    assert "tier2" not in batch
    assert batch["envelope"]["tiers_included"] == [1]


def test_tier2_present_when_consented_and_content_free():
    secret = "SECRET-SSN-123-45-6789"
    _seed_dlp_alert("us_ssn", "pii", "US_SSN", secret, score=0.92)

    settings.set_value("TELEMETRY_TIER2_ENABLED", "true", None)
    try:
        batch = telemetry.build_payload(0.0, time.time() + 1)
    finally:
        settings.reset("TELEMETRY_TIER2_ENABLED")

    assert batch["envelope"]["tiers_included"] == [1, 2]
    t2 = batch["tier2"]
    assert t2["tier"] == 2
    assert t2["consent"]["granted"] is True
    # Derived features are counted…
    assert t2["dlp_pattern_hits"].get("us_ssn") == 1
    assert t2["dlp_finding_categories"].get("pii") == 1
    assert t2["dlp_entity_types"].get("US_SSN") == 1
    assert t2["dlp_score_histogram"].get("0.9-1.0") == 1
    # …but the raw matched value never leaks, and the signature still covers it.
    assert secret not in json.dumps(batch)
    assert "matched_value" not in json.dumps(batch)


def test_maybe_emit_noops_when_disabled(monkeypatch):
    called = {"emit": False}

    async def _spy():
        called["emit"] = True
        return {}

    monkeypatch.setattr(telemetry, "emit_once", _spy)
    monkeypatch.setattr(
        telemetry.settings,
        "get",
        lambda k: False if k == "TELEMETRY_ENABLED" else "x",
    )
    asyncio.run(telemetry._maybe_emit())
    assert called["emit"] is False


def _enabled_settings(interval_hours: float = 1.0):
    def _get(key):
        return {
            "TELEMETRY_ENABLED": True,
            "TELEMETRY_ENDPOINT": "https://collector.example/v1/ingest",
            "TELEMETRY_INTERVAL_HOURS": interval_hours,
        }.get(key)

    return _get


def test_maybe_emit_skips_inside_interval(monkeypatch):
    called = {"emit": False}

    async def _spy():
        called["emit"] = True
        return {}

    monkeypatch.setattr(telemetry, "emit_once", _spy)
    monkeypatch.setattr(telemetry.settings, "get", _enabled_settings())
    _reset_watermark(time.time())  # just sent — interval has not elapsed
    asyncio.run(telemetry._maybe_emit())
    assert called["emit"] is False


def test_maybe_emit_emits_once_interval_elapsed(monkeypatch):
    called = {"emit": False}

    async def _spy():
        called["emit"] = True
        return {}

    monkeypatch.setattr(telemetry, "emit_once", _spy)
    monkeypatch.setattr(telemetry.settings, "get", _enabled_settings())
    _reset_watermark(0.0)
    asyncio.run(telemetry._maybe_emit())
    assert called["emit"] is True


# ---------------------------------------------------------------------------
# Transport key lifecycle
# ---------------------------------------------------------------------------


def test_transport_key_generated_once_then_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "KEY_DIR", tmp_path)
    monkeypatch.setattr(
        telemetry, "TRANSPORT_KEY_PATH", tmp_path / "telemetry_transport.key"
    )
    monkeypatch.setattr(telemetry, "_private_key", None)

    key = telemetry.ensure_transport_key()
    path = telemetry.TRANSPORT_KEY_PATH
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    # Cached instance is returned without re-reading.
    assert telemetry.ensure_transport_key() is key

    # A fresh process (cache cleared) loads the SAME key from disk.
    monkeypatch.setattr(telemetry, "_private_key", None)
    reloaded = telemetry.ensure_transport_key()
    assert (
        reloaded.public_key().public_bytes_raw() == key.public_key().public_bytes_raw()
    )


# ---------------------------------------------------------------------------
# Delivery retries
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient — scripted responses per attempt."""

    calls = 0
    fail_times = 0

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, endpoint, json=None):
        cls = type(self)
        cls.calls += 1
        if cls.calls <= cls.fail_times:
            raise RuntimeError(f"connect refused (attempt {cls.calls})")

        class _Resp:
            def raise_for_status(self):
                pass

        return _Resp()


def test_post_batch_retries_then_succeeds(monkeypatch):
    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.fail_times = 1
    monkeypatch.setattr(telemetry.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(telemetry, "_SEND_BACKOFF_S", 0.0)

    ok, err = asyncio.run(telemetry._post_batch("https://x.example", {"a": 1}))
    assert ok is True and err == ""
    assert _FakeAsyncClient.calls == 2  # one failure, one success


def test_post_batch_gives_up_after_all_retries(monkeypatch):
    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.fail_times = 99
    monkeypatch.setattr(telemetry.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(telemetry, "_SEND_BACKOFF_S", 0.0)

    ok, err = asyncio.run(telemetry._post_batch("https://x.example", {"a": 1}))
    assert ok is False
    assert "connect refused" in err
    assert _FakeAsyncClient.calls == telemetry._SEND_RETRIES


# ---------------------------------------------------------------------------
# Worker task
# ---------------------------------------------------------------------------


def test_worker_starts_once_and_survives_crashes(monkeypatch):
    crashes = {"n": 0}

    async def _boom():
        crashes["n"] += 1
        raise RuntimeError("cycle crashed")

    monkeypatch.setattr(telemetry, "_maybe_emit", _boom)
    monkeypatch.setattr(telemetry, "_TICK_SECONDS", 0.01)
    monkeypatch.setattr(telemetry, "_worker_task", None)

    async def _run():
        t1 = telemetry.start_telemetry_worker()
        t2 = telemetry.start_telemetry_worker()
        assert t1 is t2  # idempotent
        await asyncio.sleep(0.05)
        assert not t1.done()  # the loop swallowed the crash and kept running
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1

    asyncio.run(_run())
    assert crashes["n"] >= 2
