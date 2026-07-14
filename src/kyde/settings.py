"""
Runtime-tunable settings.

Resolution order for each key: DB row → env var → hard-coded default.
Each read is cached in-process for `_CACHE_TTL` seconds so the proxy's hot
scan path doesn't hit Postgres on every request. Writes invalidate the
cache immediately.

The whitelist below is intentionally tiny — only values that are:
  (a) consumed by our own container (no coordinated change needed in
      dlp-bert / dlp-regex / postgres), and
  (b) meaningful for an operator to change at runtime.

To expose a new setting, add a `SettingSpec` entry and make sure the
consumer reads through `get_*` rather than os.getenv at import time.
"""

from __future__ import annotations

import ipaddress
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Optional

from . import ledger


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    description: str
    type: str  # "float" | "int" | "bool" | "string"
    default: Any
    # Called with the decoded value; raise ValueError to reject.
    validate: Optional[Callable[[Any], None]] = None


def _in_range(lo: float, hi: float) -> Callable[[float], None]:
    def check(v: float) -> None:
        if v < lo or v > hi:
            raise ValueError(f"must be between {lo} and {hi}")

    return check


def _one_of(*choices: str) -> Callable[[str], None]:
    allowed = set(choices)

    def check(v: str) -> None:
        if v not in allowed:
            raise ValueError(f"must be one of: {sorted(allowed)}")

    return check


def _smtp_port_range(v: int) -> None:
    if v < 1 or v > 65535:
        raise ValueError("must be between 1 and 65535")


def _cidr_list(v: str) -> None:
    """Accept a comma-separated list of CIDR ranges. Blank = treat as empty list."""
    for token in (v or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid CIDR {token!r}: {exc}") from exc


# Whitelist. Only keys listed here are readable/writable via /api/settings.
SPECS: dict[str, SettingSpec] = {
    "DLP_BERT_THRESHOLD": SettingSpec(
        key="DLP_BERT_THRESHOLD",
        label="DLP BERT threshold",
        description=(
            "Minimum neural classifier confidence (0.0–1.0) required to "
            "persist a DLP alert. Raise to filter noise; lower to surface "
            "more borderline findings."
        ),
        type="float",
        default=0.5,
        validate=_in_range(0.0, 1.0),
    ),
    "DLP_REGEX_THRESHOLD": SettingSpec(
        key="DLP_REGEX_THRESHOLD",
        label="DLP regex threshold",
        description=(
            "Minimum regex-scanner score (0.0–1.0) required to persist a "
            "DLP alert. Most regex findings score ≥ 0.5; raising this "
            "suppresses lower-confidence patterns."
        ),
        type="float",
        default=0.7,
        validate=_in_range(0.0, 1.0),
    ),
    "DLP_REGEX_PREVENTION": SettingSpec(
        key="DLP_REGEX_PREVENTION",
        label="Policy Prevention (regex)",
        description=(
            "When on, a regex hit from a prevention-enabled pattern at/above "
            "the regex threshold BLOCKS the request inline (403) instead of "
            "only alerting. Prevention is opt-in per pattern on the Policies "
            "page. Off = detect-only (today's behavior). Scanner outages "
            "fail open with a high-severity incident."
        ),
        type="bool",
        default=False,
    ),
    "DLP_BERT_PREVENTION": SettingSpec(
        key="DLP_BERT_PREVENTION",
        label="BERT Prevention",
        description=(
            "When on, a BERT finding at/above the BERT threshold BLOCKS the "
            "request inline (403) instead of only alerting. Off = "
            "detect-only. Scanner outages fail open with a high-severity "
            "incident."
        ),
        type="bool",
        default=False,
    ),
    # ── Host resolution (reverse-DNS cache; see host_resolver.py).
    "HOST_DNS_TIMEOUT_SECONDS": SettingSpec(
        key="HOST_DNS_TIMEOUT_SECONDS",
        label="Reverse-DNS timeout",
        description=(
            "Maximum seconds the lazy reverse-DNS resolver waits before "
            "giving up on one IP. Hits cache as a miss (with the shorter "
            "miss TTL) so a slow resolver doesn't keep retrying."
        ),
        type="float",
        default=0.5,
        validate=_in_range(0.05, 10.0),
    ),
    "HOST_DNS_TTL_HIT_SECONDS": SettingSpec(
        key="HOST_DNS_TTL_HIT_SECONDS",
        label="Reverse-DNS TTL (hit)",
        description=(
            "How long a successfully-resolved hostname stays in the cache "
            "before the next read triggers a refresh. 24 hours by default."
        ),
        type="int",
        default=86400,
        validate=_in_range(60, 2_592_000),
    ),
    "HOST_DNS_TTL_MISS_SECONDS": SettingSpec(
        key="HOST_DNS_TTL_MISS_SECONDS",
        label="Reverse-DNS TTL (miss)",
        description=(
            "How long an unresolved IP stays cached as a miss before the "
            "next read retries. 1 hour by default; shorten if you expect "
            "PTR records to appear soon after a host comes online."
        ),
        type="int",
        default=3600,
        validate=_in_range(60, 86400),
    ),
    # ── Public-URL hints. These don't affect backend behavior; they drive
    # the "Agent endpoints" section of the Settings page so operators can
    # hand agents a copy-paste URL that matches how the service is reached
    # from the corporate network (scheme/host/port may differ from what
    # the admin's browser sees — e.g. they admin over a VPN, agents via a
    # public hostname behind a TLS terminator).
    "PUBLIC_PROTOCOL": SettingSpec(
        key="PUBLIC_PROTOCOL",
        label="Public protocol",
        description=(
            "http or https — the scheme agents should use to reach this " "gateway."
        ),
        type="string",
        default="http",
        validate=lambda v: (
            None
            if v in ("", "http", "https")
            else (_ for _ in ()).throw(ValueError("must be 'http', 'https', or blank"))
        ),
    ),
    "PUBLIC_HOSTNAME": SettingSpec(
        key="PUBLIC_HOSTNAME",
        label="Public hostname",
        description=(
            "Hostname or IP agents should use to reach this gateway — e.g. "
            "'kyde.internal' or 'gateway.example.com'."
        ),
        type="string",
        default="localhost",
    ),
    "PUBLIC_PORT": SettingSpec(
        key="PUBLIC_PORT",
        label="Public port",
        description=(
            "Port agents should use to reach the LLM proxy. The proxy "
            "listens on its own port (4000 by default) separate from the "
            "admin UI, so /v1/* paths can't collide with the admin "
            "/api/* paths."
        ),
        type="string",
        default="4000",
    ),
    # ── SMTP notifications. Auditor users (role='auditor') receive an
    # email on each first-detected DLP alert. A polling worker in the
    # kyde-api container reads these settings and applies the trigger
    # policy; the proxy only sets email_status='pending' on new alerts.
    "SMTP_ENABLED": SettingSpec(
        key="SMTP_ENABLED",
        label="SMTP notifications enabled",
        description=(
            "Master kill-switch for alert emails. When false, the "
            "notification worker skips all pending rows."
        ),
        type="bool",
        default=False,
    ),
    "SMTP_HOST": SettingSpec(
        key="SMTP_HOST",
        label="SMTP host",
        description="SMTP relay hostname (e.g. smtp.sendgrid.net).",
        type="string",
        default="",
    ),
    "SMTP_PORT": SettingSpec(
        key="SMTP_PORT",
        label="SMTP port",
        description=(
            "587 for STARTTLS submission (recommended), 465 for implicit "
            "TLS (SMTPS), 25 for plaintext (not recommended). The "
            "encryption mode is set separately."
        ),
        type="int",
        default=587,
        validate=_smtp_port_range,
    ),
    "SMTP_ENCRYPTION": SettingSpec(
        key="SMTP_ENCRYPTION",
        label="SMTP encryption",
        description=(
            "'starttls' (upgrade a plain connection — port 587), 'tls' "
            "(implicit TLS from the first byte — port 465), or 'none' "
            "(plaintext — port 25, test/dev only)."
        ),
        type="string",
        default="starttls",
        validate=_one_of("none", "starttls", "tls"),
    ),
    "SMTP_USERNAME": SettingSpec(
        key="SMTP_USERNAME",
        label="SMTP username",
        description=(
            "Auth username. Leave blank for IP-authenticated relays "
            "that don't require SMTP AUTH."
        ),
        type="string",
        default="",
    ),
    "SMTP_PASSWORD_ENC": SettingSpec(
        key="SMTP_PASSWORD_ENC",
        label="SMTP password (encrypted)",
        description=(
            "AES-GCM-256 ciphertext of the SMTP password. The plaintext "
            "is never stored or returned by the API. Leave blank on save "
            "to keep the existing value unchanged."
        ),
        type="string",
        default="",
    ),
    "SMTP_FROM_ADDRESS": SettingSpec(
        key="SMTP_FROM_ADDRESS",
        label="From address",
        description=(
            "Envelope-from and header-From address for alert emails. "
            "Usually must match a domain your relay is authorized to send for."
        ),
        type="string",
        default="",
    ),
    "SMTP_FROM_NAME": SettingSpec(
        key="SMTP_FROM_NAME",
        label="From display name",
        description="Display name shown to auditors in their mail client.",
        type="string",
        default="Kyde Gateway Alerts",
    ),
    "SMTP_REPLY_TO": SettingSpec(
        key="SMTP_REPLY_TO",
        label="Reply-To address",
        description=(
            "Optional Reply-To header. Useful when alerts go from a "
            "no-reply@ but replies should reach a security inbox."
        ),
        type="string",
        default="",
    ),
    "SMTP_TLS_VERIFY": SettingSpec(
        key="SMTP_TLS_VERIFY",
        label="Verify TLS certificate",
        description=(
            "Verify the relay's TLS certificate chain. Disable only if "
            "your relay uses a self-signed cert that isn't in the system "
            "trust store."
        ),
        type="bool",
        default=True,
    ),
    "SMTP_TIMEOUT_SECONDS": SettingSpec(
        key="SMTP_TIMEOUT_SECONDS",
        label="SMTP timeout (seconds)",
        description=(
            "Connect + send timeout. Short enough that a hung relay "
            "doesn't block the worker for more than a poll cycle."
        ),
        type="int",
        default=10,
        validate=_in_range(1, 120),
    ),
    "SMTP_TRIGGER_POLICY": SettingSpec(
        key="SMTP_TRIGGER_POLICY",
        label="Email trigger policy",
        description=(
            "'first_detection' (send only the first time a unique leak "
            "appears — default, lowest noise), "
            "'first_detection_min_score' (same, but only if score ≥ "
            "SMTP_MIN_SCORE), or 'every_scan' (re-email on dedup repeats "
            "too — noisy)."
        ),
        type="string",
        default="first_detection",
        validate=_one_of("first_detection", "first_detection_min_score", "every_scan"),
    ),
    "SMTP_MIN_SCORE": SettingSpec(
        key="SMTP_MIN_SCORE",
        label="Minimum score for email",
        description=(
            "Score threshold used only when trigger policy = "
            "'first_detection_min_score'. Ignored otherwise."
        ),
        type="float",
        default=0.8,
        validate=_in_range(0.0, 1.0),
    ),
    # ── Agent Topology: network-origin capture. These govern how we parse
    # and trust the forwarded-proxy chain in front of the gateway. The
    # defaults are appropriate for a gateway running behind private-network
    # proxies (corp load balancers, Docker bridges, k8s ingresses). Operators
    # fronted by Cloudflare / Fastly / AWS ALB must extend the list to
    # include those edges; otherwise the displayed origin will be stuck at
    # the public edge IP rather than the real client.
    "TRUSTED_PROXY_CIDRS": SettingSpec(
        key="TRUSTED_PROXY_CIDRS",
        label="Trusted proxy CIDRs",
        description=(
            "Comma-separated CIDR ranges whose X-Forwarded-For additions "
            "we trust. The parser walks the chain right-to-left; an entry "
            "is accepted only if the hop that added it sits in one of "
            "these ranges. Everything before the first untrusted hop is "
            "discarded as client-spoofable. Defaults cover loopback + "
            "RFC1918 + v6 ULA; add your edge ranges (e.g. Cloudflare) here."
        ),
        type="string",
        default=(
            "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16," "::1/128,fc00::/7"
        ),
        validate=_cidr_list,
    ),
    "NETWORK_ORIGIN_ENABLED": SettingSpec(
        key="NETWORK_ORIGIN_ENABLED",
        label="Capture network origin",
        description=(
            "When enabled, each proxied request also writes a row to "
            "request_network with the parsed forwarded chain, classified "
            "origin, and parsed User-Agent. Powers the Agent Topology "
            "page. Disable to skip the side-table insert on the hot path."
        ),
        type="bool",
        default=True,
    ),
    # ── Telemetry emitter (see telemetry.py). Opt-in phone-home of Tier-1
    # aggregate utilization signals (install / config / usage / health) to a
    # control plane. Default OFF: nothing leaves the VPC until an operator
    # deliberately enables it and points it at an endpoint. The payload is
    # derived aggregates + pseudonymized IDs only — never prompt content.
    "TELEMETRY_ENABLED": SettingSpec(
        key="TELEMETRY_ENABLED",
        label="Usage telemetry",
        description=(
            "When enabled, the gateway periodically sends a signed batch of "
            "aggregate utilization metrics (counts, rates, feature adoption, "
            "trust scores) to TELEMETRY_ENDPOINT. No prompt content, matched "
            "secrets, or raw IDs are ever included. Off by default."
        ),
        type="bool",
        default=False,
    ),
    "TELEMETRY_ENDPOINT": SettingSpec(
        key="TELEMETRY_ENDPOINT",
        label="Telemetry endpoint",
        description=(
            "HTTPS URL that receives the signed telemetry batch (POST). "
            "Leave blank to disable delivery even when telemetry is enabled "
            "(the payload can still be inspected via the preview endpoint)."
        ),
        type="string",
        default="",
        validate=lambda v: (
            None
            if v == "" or v.startswith(("http://", "https://"))
            else (_ for _ in ()).throw(
                ValueError("must be blank or start with http:// or https://")
            )
        ),
    ),
    "TELEMETRY_INTERVAL_HOURS": SettingSpec(
        key="TELEMETRY_INTERVAL_HOURS",
        label="Telemetry interval (hours)",
        description=(
            "Minimum hours between telemetry batches. Each batch covers the "
            "window since the last successful send, so a longer interval "
            "simply produces larger, less frequent deltas."
        ),
        type="int",
        default=24,
        validate=_in_range(1, 168),
    ),
    # ── Tier 2 (content-derived features). SEPARATE, EXPLICIT CONSENT.
    # Tier 1 (above) ships only pure aggregates. Tier 2 additionally ships
    # features DERIVED from request content — per-pattern DLP hit counts,
    # matched entity types, finding categories, and score distributions.
    # These reveal more about what flowed through the gateway than a plain
    # alert count, which is why enabling this is a deliberate, auditable act
    # of consent — off by default and independent of the Tier 1 switch. It
    # still never ships raw prompts, matched secrets, or PII values. Requires
    # TELEMETRY_ENABLED to actually transmit.
    "TELEMETRY_TIER2_ENABLED": SettingSpec(
        key="TELEMETRY_TIER2_ENABLED",
        label="Telemetry: Tier 2 content-derived features (consent)",
        description=(
            "CONSENT REQUIRED. When enabled, telemetry batches include Tier 2 "
            "content-derived features: per-pattern DLP hit counts, matched "
            "entity types, finding categories, and score histograms. This is "
            "richer than the Tier 1 aggregates and is derived from request "
            "content — enabling it is your explicit consent to share it. Raw "
            "prompts, matched secrets, and PII values are STILL never sent. "
            "Off by default; has no effect unless usage telemetry is also on."
        ),
        type="bool",
        default=False,
    ),
}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE_TTL = 5.0  # seconds — short enough to feel live, long enough to
# absorb the hot path on busy proxies.
_cache: dict[str, tuple[float, Any]] = {}  # key → (expires_at, value)
_cache_lock = Lock()


def invalidate_cache(key: Optional[str] = None) -> None:
    """Drop either one key or the whole cache (after a write)."""
    with _cache_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Typed getters
# ---------------------------------------------------------------------------


def _decode(spec: SettingSpec, raw: str) -> Any:
    if spec.type == "float":
        return float(raw)
    if spec.type == "int":
        return int(raw)
    if spec.type == "bool":
        return raw.lower() in ("1", "true", "yes", "on")
    return raw


def _resolve(spec: SettingSpec) -> tuple[Any, str]:
    """Return (value, source) where source ∈ {"db", "env", "default"}."""
    row = ledger.get_setting(spec.key)
    if row is not None:
        try:
            return _decode(spec, row["value"]), "db"
        except ValueError:
            # Corrupt DB row — fall through to env/default rather than
            # bringing the proxy down. Still logged for forensics.
            print(f"  ⚠ settings: bad DB value for {spec.key!r}={row['value']!r}")
    env_raw = os.getenv(spec.key, "")
    if env_raw != "":
        try:
            return _decode(spec, env_raw), "env"
        except ValueError:
            print(f"  ⚠ settings: bad env value for {spec.key!r}={env_raw!r}")
    return spec.default, "default"


def get(key: str) -> Any:
    """Return the effective value for `key`. Unknown keys raise KeyError."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown setting: {key!r}")
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value, _source = _resolve(spec)
    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL, value)
    return value


def get_with_source(key: str) -> tuple[Any, str]:
    """Like get(), but also reports where the value came from. Bypasses cache
    (the dashboard calls this — correctness matters more than latency)."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown setting: {key!r}")
    return _resolve(spec)


def set_value(key: str, raw: str, user_id: Optional[int]) -> dict:
    """Validate and persist a new value. Returns the stored row."""
    spec = SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown setting: {key!r}")
    decoded = _decode(spec, raw)
    if spec.validate is not None:
        spec.validate(decoded)
    row = ledger.upsert_setting(spec.key, raw, user_id)
    invalidate_cache(key)
    return row


def reset(key: str) -> bool:
    """Clear the DB override so env/default takes over."""
    if key not in SPECS:
        raise KeyError(f"unknown setting: {key!r}")
    deleted = ledger.delete_setting(key)
    invalidate_cache(key)
    return deleted


def list_all() -> list[dict]:
    """Snapshot of every whitelisted setting with its effective value + source."""
    rows = ledger.list_settings(list(SPECS.keys()))
    out: list[dict] = []
    for key, spec in SPECS.items():
        value, source = _resolve(spec)
        stored = rows.get(key)
        out.append(
            {
                "key": key,
                "label": spec.label,
                "description": spec.description,
                "type": spec.type,
                "default": spec.default,
                "value": value,
                "source": source,
                "updated_at": stored["updated_at"] if stored else None,
                "updated_by": stored["updated_by"] if stored else None,
            }
        )
    return out
