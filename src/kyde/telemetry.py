"""
Opt-in telemetry emitter — periodically phones home Tier-1 aggregate
utilization signals (installation, configuration, usage, health) about this
gateway deployment.

Runs inside the kyde-api (dashboard) container: one asyncio task started from
the FastAPI lifespan, mirroring `notifications.py`. The proxy (kyde-gateway)
is untouched.

Hard privacy rules (this product's promise is "prompts never leave the VPC"):

  * Only DERIVED AGGREGATES leave — counts, sums, rates, category labels.
    Never `full_messages` / `why` / DLP `findings` / `client_ip` / raw IDs.
  * Identifiers are HMAC-pseudonymized with a per-deploy salt before they
    leave (gateway_id). The salt lives in `telemetry_state` (0021 migration).
  * Timestamps are coarsened to the hour.
  * Every batch is Ed25519-signed with a per-deploy transport key so the
    control plane can authenticate the sender. This key is INDEPENDENT of the
    enterprise `kyde.signing` module (absent in the starter edition), so the
    emitter works in both editions.

Delivery is a delta: each batch covers the window since the last SUCCESSFUL
send (a DB watermark, restart-safe). The watermark only advances on a 2xx, so
a failed send re-sends the same window next cycle — no gaps, no double-count.

Everything is opt-in: nothing is built or sent until an operator sets
TELEMETRY_ENABLED and TELEMETRY_ENDPOINT.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import socket
import time
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import _features, ledger, settings, trust
from .crypto import KEY_DIR

# The transport key lives next to the AES + signing material in the shared
# kyde-store volume, so it survives restarts and stays out of the DB.
TRANSPORT_KEY_PATH = KEY_DIR / "telemetry_transport.key"

SCHEMA_VERSION = 1
SERVICE_VERSION = "0.1.0"  # matches dashboard.api_configuration; no git-SHA stamp yet

_TICK_SECONDS = 60.0  # worker wakes this often; only emits when the interval elapses
_HTTP_TIMEOUT_S = 15.0
_SEND_RETRIES = 3
_SEND_BACKOFF_S = 5.0

# Config keys whose VALUE is sensitive (secret, hostname, or network topology):
# we report only whether they are set, never the value.
_CONFIG_PRESENCE_ONLY = {
    "SMTP_PASSWORD_ENC",
    "SMTP_USERNAME",
    "SMTP_HOST",
    "SMTP_FROM_ADDRESS",
    "SMTP_REPLY_TO",
    "PUBLIC_HOSTNAME",
    "TRUSTED_PROXY_CIDRS",
    "TELEMETRY_ENDPOINT",
}

_worker_task: Optional[asyncio.Task] = None
_private_key: Optional[Ed25519PrivateKey] = None
_salt_cache: Optional[bytes] = None


# ---------------------------------------------------------------------------
# Transport key (Ed25519, edition-independent)
# ---------------------------------------------------------------------------


def ensure_transport_key() -> Ed25519PrivateKey:
    """Return the Ed25519 transport private key, generating it once if absent.

    Idempotent and modeled on `crypto.ensure_aes_key`: atomic tmp+rename,
    0o600, refuses nothing (a malformed file raises rather than being
    silently replaced). Built on `cryptography` directly — NOT `kyde.signing`,
    which is absent in the starter edition.
    """
    global _private_key
    if _private_key is not None:
        return _private_key
    if TRANSPORT_KEY_PATH.exists():
        _private_key = serialization.load_pem_private_key(
            TRANSPORT_KEY_PATH.read_bytes(), password=None
        )  # type: ignore[assignment]
        return _private_key
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        KEY_DIR.chmod(0o700)
    except PermissionError:
        pass
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    tmp = TRANSPORT_KEY_PATH.with_suffix(".tmp")
    tmp.write_bytes(pem)
    tmp.chmod(0o600)
    tmp.rename(TRANSPORT_KEY_PATH)
    _private_key = key
    return key


def _public_pem() -> bytes:
    return (
        ensure_transport_key()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def transport_fingerprint() -> str:
    """SHA-256 of the public key PEM, first 32 hex chars.

    Mirrors `signing.public_key_fingerprint` so the control plane can pin the
    sender's key with the same convention used elsewhere.
    """
    return hashlib.sha256(_public_pem()).hexdigest()[:32]


def _canonical_bytes(obj: Any) -> bytes:
    """Project-wide canonical JSON (matches ledger._hash_dict / signing)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(payload: dict) -> str:
    return base64.b64encode(
        ensure_transport_key().sign(_canonical_bytes(payload))
    ).decode("ascii")


# ---------------------------------------------------------------------------
# Pseudonymization + time coarsening
# ---------------------------------------------------------------------------


def _salt() -> bytes:
    global _salt_cache
    if _salt_cache is None:
        _salt_cache = ledger.ensure_telemetry_salt()
    return _salt_cache


def _pseudonym(value: str) -> str:
    """HMAC-SHA256(salt, value) hex — stable per deploy, opaque to the vendor."""
    return hmac.new(_salt(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _hour_floor(ts: float) -> int:
    return int(ts // 3600 * 3600)


# ---------------------------------------------------------------------------
# Payload sections
# ---------------------------------------------------------------------------


def _install_section() -> dict:
    from . import server as _server  # lazy: avoid proxy import at module load

    providers = sorted(_server.UPSTREAMS.keys())
    return {
        "edition": _features.edition(),
        "signing_enabled": _features.HAS_SIGNING,
        "enforcement_enabled": _features.HAS_ENFORCEMENT,
        "service_version": SERVICE_VERSION,
        "telemetry_schema_version": SCHEMA_VERSION,
        "providers_configured": providers,
    }


def _config_section() -> dict:
    """Feature-adoption snapshot: which settings are set to what, and from where.

    Sensitive keys collapse to a presence boolean; everything else reports its
    typed value plus source (db/env/default) so we can see what operators
    actually tune vs. leave at default.
    """
    out: dict[str, Any] = {}
    for spec in settings.list_all():
        key = spec["key"]
        if key in _CONFIG_PRESENCE_ONLY:
            out[key] = {"is_set": bool(spec.get("value")), "source": spec.get("source")}
        else:
            out[key] = {"value": spec.get("value"), "source": spec.get("source")}
    out["counts"] = {
        "mcp_servers": ledger.count_rows("mcp_servers"),
        "dlp_rules": ledger.count_rows("dlp_rules"),
        "dlp_prevention_patterns": ledger.count_rows("dlp_prevention_patterns"),
        "users": ledger.count_rows("users"),
    }
    return out


def _usage_section(since: float, until: float) -> dict:
    since_arg = since if since > 0 else None

    # Request stats (rollup mirrors dashboard.api_stats, windowed to (since, until]).
    stats_rows = [
        r for r in ledger.get_stats_rows(since=since_arg) if r["timestamp"] <= until
    ]
    by_action: dict[str, int] = {}
    by_upstream: dict[str, int] = {}
    agents: set[str] = set()
    sessions: set[str] = set()
    for r in stats_rows:
        agents.add(r.get("agent_id", ""))
        sessions.add(r.get("session_id", ""))
        by_action[r["action_type"]] = by_action.get(r["action_type"], 0) + 1
        up = r.get("upstream", "") or "(none)"
        by_upstream[up] = by_upstream.get(up, 0) + 1

    # Tokens (rollup mirrors dashboard.api_token_analysis).
    tok_rows = [
        r
        for r in ledger.get_token_analysis_rows(since=since_arg)
        if r["timestamp"] <= until
    ]
    prompt = sum(int(r.get("prompt_tokens", 0) or 0) for r in tok_rows)
    completion = sum(int(r.get("completion_tokens", 0) or 0) for r in tok_rows)
    by_model: dict[str, int] = {}
    for r in tok_rows:
        m = r.get("model", "") or "(unknown)"
        by_model[m] = (
            by_model.get(m, 0)
            + int(r.get("prompt_tokens", 0) or 0)
            + int(r.get("completion_tokens", 0) or 0)
        )

    # DLP alert label counts (created_at >= since; content never included).
    dlp_disposition: dict[str, int] = {}
    dlp_severity: dict[str, int] = {}
    dlp_scanner: dict[str, int] = {}
    dlp_source: dict[str, int] = {}
    dlp_prevented = 0
    for row in ledger.count_dlp_alerts_grouped(since=since_arg):
        n = int(row["n"])
        dlp_disposition[row["disposition"] or "open"] = (
            dlp_disposition.get(row["disposition"] or "open", 0) + n
        )
        dlp_severity[row["severity"]] = dlp_severity.get(row["severity"], 0) + n
        dlp_scanner[row["scanner"]] = dlp_scanner.get(row["scanner"], 0) + n
        dlp_source[row["source_type"]] = dlp_source.get(row["source_type"], 0) + n
        if row["prevented"]:
            dlp_prevented += n

    return {
        "requests_total": len(stats_rows),
        "unique_agents": len({a for a in agents if a}),
        "unique_sessions": len({s for s in sessions if s}),
        "by_action_type": by_action,
        "by_upstream": by_upstream,
        "tokens": {
            "prompt": prompt,
            "completion": completion,
            "total": prompt + completion,
            "by_model": by_model,
        },
        "dlp_alerts_by_disposition": dlp_disposition,
        "dlp_alerts_by_severity": dlp_severity,
        "dlp_alerts_by_scanner": dlp_scanner,
        "dlp_alerts_by_source_type": dlp_source,
        "dlp_prevented_count": dlp_prevented,
    }


def _health_section(since: float) -> dict:
    since_arg = since if since > 0 else None
    valid, errors = ledger.verify_chain(record=False)
    fleet = trust.fleet_trust(since_arg, signing_enabled=_features.HAS_SIGNING)
    return {
        "ledger_size_bytes": ledger.database_size_bytes(),
        "total_entries": ledger.count_entries(),
        "chain_valid": valid,
        "chain_error_count": len(errors),
        "fleet_trust": {
            "composite": fleet.get("trust_score"),
            "active_agents": fleet.get("active_agents"),
            "dimensions": fleet.get("dimensions"),
        },
    }


def _tier2_consent() -> dict:
    """Consent provenance for the Tier 2 toggle — who granted it and when.

    Read from the setting's own audit columns so the control plane can see
    that content-derived collection was a deliberate, attributable choice.
    """
    for spec in settings.list_all():
        if spec["key"] == "TELEMETRY_TIER2_ENABLED":
            return {
                "granted": bool(spec.get("value")),
                "granted_at": spec.get("updated_at"),
                "granted_by": spec.get("updated_by"),
            }
    return {"granted": False, "granted_at": None, "granted_by": None}


def _tier2_section(since: float) -> dict:
    """Tier 2 (content-derived) DLP features. Consent-gated; content-free.

    Everything here is derived from finding metadata (which patterns/entities
    fired, at what scores) — never the matched values themselves.
    """
    since_arg = since if since > 0 else None
    features = ledger.get_dlp_tier2_features(since=since_arg)
    return {
        "tier": 2,
        "consent": _tier2_consent(),
        "dlp_pattern_hits": features["pattern_hits"],
        "dlp_entity_types": features["entity_types"],
        "dlp_finding_categories": features["categories"],
        "dlp_score_histogram": features["score_histogram"],
    }


def build_payload(since: float, until: float) -> dict:
    """Assemble and sign the telemetry batch for the window (since, until].

    Always includes Tier 1 (pure aggregates). Includes a Tier 2 section —
    content-DERIVED features — only when TELEMETRY_TIER2_ENABLED consent is
    on. Returns the full batch INCLUDING the Ed25519 signature over the
    canonical unsigned payload. Never contains raw content or raw identifiers.
    """
    tier2_on = bool(settings.get("TELEMETRY_TIER2_ENABLED"))
    gateway_id = _pseudonym(socket.gethostname())
    payload = {
        "envelope": {
            "schema_version": SCHEMA_VERSION,
            "batch_kind": "delta",
            "tiers_included": [1, 2] if tier2_on else [1],
            "gateway_id": gateway_id,
            "window_start": _hour_floor(since),
            "window_end": _hour_floor(until),
            "edition": _features.edition(),
            "service_version": SERVICE_VERSION,
            "transport_key_fp": transport_fingerprint(),
        },
        "install": _install_section(),
        "config": _config_section(),
        "usage": _usage_section(since, until),
        "health": _health_section(since),
    }
    if tier2_on:
        payload["tier2"] = _tier2_section(since)
    return {**payload, "signature": _sign(payload)}


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def _post_batch(endpoint: str, batch: dict) -> tuple[bool, str]:
    """POST the batch with bounded retries. Never raises; returns (ok, error)."""
    last_err = ""
    for attempt in range(1, _SEND_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(endpoint, json=batch)
                resp.raise_for_status()
            return True, ""
        except Exception as e:  # noqa: BLE001 — delivery must tolerate anything
            last_err = str(e)
            print(f"  ⚠ telemetry: send attempt {attempt}/{_SEND_RETRIES} failed: {e}")
            if attempt < _SEND_RETRIES:
                await asyncio.sleep(_SEND_BACKOFF_S)
    return False, last_err


async def emit_once() -> dict:
    """Build, send, and (on success) advance the watermark. Never raises.

    Returns a small status dict for the admin `send-now` endpoint.
    """
    now = time.time()
    state = ledger.get_telemetry_state()
    last_sent = float(state.get("last_sent") or 0.0)
    endpoint = str(settings.get("TELEMETRY_ENDPOINT") or "")

    batch = await asyncio.to_thread(build_payload, last_sent, now)

    if not endpoint:
        ledger.set_telemetry_last_sent(last_sent, "no_endpoint", "")
        return {"status": "no_endpoint", "window_start": last_sent}

    ok, err = await _post_batch(endpoint, batch)
    if ok:
        ledger.set_telemetry_last_sent(now, "ok", "")
        print(f"  ✓ telemetry: batch sent, window advanced to {int(now)}")
        return {"status": "ok", "window_start": last_sent, "window_end": now}
    ledger.set_telemetry_last_sent(last_sent, "error", err)
    return {"status": "error", "error": err, "window_start": last_sent}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _maybe_emit() -> None:
    if not bool(settings.get("TELEMETRY_ENABLED")):
        return
    if not str(settings.get("TELEMETRY_ENDPOINT") or ""):
        return
    interval_s = float(settings.get("TELEMETRY_INTERVAL_HOURS")) * 3600.0
    state = ledger.get_telemetry_state()
    if time.time() - float(state.get("last_sent") or 0.0) < interval_s:
        return
    await emit_once()


async def _worker_loop() -> None:
    while True:
        try:
            await _maybe_emit()
        except Exception as e:  # belt-and-braces: the loop must never die
            print(f"  ⚠ telemetry: emit cycle crashed — {e}")
        await asyncio.sleep(_TICK_SECONDS)


def start_telemetry_worker() -> Optional[asyncio.Task]:
    """Idempotent. Safe to call multiple times; only the first spawns a task."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    loop = asyncio.get_event_loop()
    _worker_task = loop.create_task(_worker_loop())
    return _worker_task
