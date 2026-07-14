"""
Agent Behavioral Ledger — Compliance & Audit Dashboard

FastAPI application serving a modern audit dashboard for external stakeholders.
Design language matches kyde.com (Inter font, zinc palette, sharp corners).

Run with:  python proxy.py dashboard
    or:    uvicorn dashboard:app --port 8501
"""

import json
import secrets
import time
import uuid
from collections import deque
from datetime import datetime

from contextlib import asynccontextmanager

import httpx
from fastapi import Cookie, FastAPI, Query, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn

from . import (
    audit_log,
    auth,
    crypto,
    dlp,
    dlp_triage,
    ledger,
    notifications,
    smtp_sender,
    telemetry,
)
from . import settings as settings_module
from . import _features

# Signing is an enterprise feature shipped as a removable module. In the sandbox
# edition it is absent; keep the names defined as no-op fallbacks so this
# module imports, and guard every use with _features.HAS_SIGNING.
if _features.HAS_SIGNING:
    from .signing import verify_payload, public_key_fingerprint, _TPM_AVAILABLE
else:
    verify_payload = None  # type: ignore[assignment]
    public_key_fingerprint = None  # type: ignore[assignment]
    _TPM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Auth — DB-backed users with three roles (admin, viewer, auditor).
# ---------------------------------------------------------------------------
# Users live in the `users` table of ledger.db. The bootstrap flow (/setup)
# creates the first admin on fresh installs. See docs/deployment.md for the full
# flow. `kyde admin create-admin` is the CLI recovery path when all
# admins are locked out.

# Sessions live in Postgres (`sessions` table, migration 0018) so they
# survive kyde-api restarts. The helpers below are thin pass-throughs to
# ledger.get_session / ledger.delete_session / etc — kept here so the
# call-site shape (dozens of `_session_ctx(token)` callers) doesn't move.

# ITIL Phase 1: Incident store and service tracking
SERVICE_START_TIME: float = time.time()
INCIDENT_STORE: deque = deque(maxlen=500)
_KNOWN_INCIDENT_ENTRIES: set[str] = set()


def _session_ctx(session_token: str | None) -> dict | None:
    if not session_token:
        return None
    return ledger.get_session(session_token)


def _check_session(session_token: str | None) -> bool:
    return _session_ctx(session_token) is not None


def _session_roles(session_token: str | None) -> list[str]:
    ctx = _session_ctx(session_token)
    return list(ctx["roles"]) if ctx else []


def _has_role(session_token: str | None, role: str) -> bool:
    return role in _session_roles(session_token)


def _is_admin(session_token: str | None) -> bool:
    return _has_role(session_token, "admin")


def _is_auditor(session_token: str | None) -> bool:
    """Only the explicit auditor role grants message-body access — admins do NOT."""
    return _has_role(session_token, "auditor")


def _is_admin_or_auditor(session_token: str | None) -> bool:
    """Compliance-handoff exports (metadata-only ledger CSV, chain signature
    archives) are open to either role. The Compliance page itself is
    routed under RequireAuditor which already accepts admin, so this
    matches what the UI presents."""
    return _is_admin(session_token) or _is_auditor(session_token)


def _is_viewer(session_token: str | None) -> bool:
    """Any authenticated user with any role can browse viewer-level pages."""
    roles = _session_roles(session_token)
    return bool(roles)


def _invalidate_user_sessions(user_id: int) -> None:
    """Drop every session token for this user (on delete / reset-password / role change)."""
    ledger.delete_sessions_for_user(user_id)


def _refresh_session(session_token: str | None) -> None:
    """Re-read the user from DB and patch the session's cached roles / flag.

    Used after self-service updates so the current tab sees the change without
    logging out and back in.
    """
    if not session_token:
        return
    ctx = ledger.get_session(session_token)
    if not ctx:
        return
    user = ledger.get_user_by_id(ctx["user_id"])
    if not user or user["deleted"] or not user["enabled"]:
        ledger.delete_session(session_token)
        return
    ledger.update_session_context(
        session_token,
        username=user["username"],
        roles=list(user["roles"]),
        must_change_password=bool(user["must_change_password"]),
    )


def _emit_incident(severity: str, component: str, description: str) -> dict:
    """Append a new open incident to the in-memory store and return it."""
    inc = {
        "id": "inc-" + str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "severity": severity,  # critical | high | medium | low
        "component": component,  # ledger | signing | tpm | proxy
        "description": description,
        "status": "open",
    }
    INCIDENT_STORE.append(inc)
    return inc


def _sync_chain_incidents(errors: list[str]) -> None:
    """Emit incidents for chain errors not yet seen, deduplicating by entry_id."""
    for err in errors:
        if not err.startswith("["):
            continue
        try:
            entry_id = err[1 : err.index("]")]
        except (ValueError, IndexError):
            continue
        if entry_id in _KNOWN_INCIDENT_ENTRIES:
            continue
        _KNOWN_INCIDENT_ENTRIES.add(entry_id)
        if "Chain break" in err:
            _emit_incident("critical", "ledger", f"Chain break detected: {err}")
        elif "Invalid signature" in err:
            _emit_incident("medium", "signing", f"Signature failure: {err}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Ensure the AES-GCM key for encrypted settings exists before any
    # request lands. The file lives in the shared kyde-store volume so
    # it persists across restarts.
    try:
        crypto.ensure_aes_key()
        print("  ✓ crypto: AES key ready")
    except Exception as e:
        print(f"  ⚠ crypto: failed to ensure AES key — {e}")
    # SMTP alert worker. Fire-and-forget asyncio task; the function is
    # idempotent so accidental double-start is harmless.
    try:
        notifications.start_notification_worker()
        print("  ✓ notifications: worker started")
    except Exception as e:
        print(f"  ⚠ notifications: worker failed to start — {e}")
    # Telemetry emitter. Opt-in (default off) and idempotent; the worker
    # itself no-ops each cycle until TELEMETRY_ENABLED is set, so starting it
    # unconditionally is harmless.
    try:
        telemetry.start_telemetry_worker()
        print("  ✓ telemetry: worker started")
    except Exception as e:
        print(f"  ⚠ telemetry: worker failed to start — {e}")
    yield


app = FastAPI(title="KYDE Gateway — Audit Dashboard", lifespan=_lifespan)


# Paths that never require authentication.
#
# `/openapi.json` is the FastAPI-generated schema. It exposes the API
# surface but no user data; we leave it public so the frontend's OpenAPI
# type generator (`npm run openapi:sync`) works without a login cookie.
_PUBLIC_PATHS = (
    "/login",
    "/setup",
    "/favicon.ico",
    "/_stcore",
    "/openapi.json",
)

# Paths reachable while `must_change_password` is True (besides public ones).
_FORCED_CHANGE_ALLOWED = (
    "/change-password",
    "/api/change-password",
    "/api/whoami",
    "/logout",
)


def _strip_root(request: Request) -> tuple[str, str]:
    full_path = request.scope.get("path", request.url.path)
    root = request.scope.get("root_path", "")
    path = full_path[len(root) :] if root and full_path.startswith(root) else full_path
    return path, root


def _is_public(path: str) -> bool:
    return any(
        path == p or path.startswith(p + "/") or path.startswith(p)
        for p in _PUBLIC_PATHS
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Three-phase gate: bootstrap → session → forced-password-change."""
    path, root = _strip_root(request)
    is_api = path.startswith("/api/")

    # Phase 1: bootstrap. If no admin exists yet, everyone is routed to /setup.
    try:
        has_admin = ledger.any_admin_exists()
    except Exception:
        has_admin = True  # fail open on DB errors — login will still block unauthenticated users
    if not has_admin and not _is_public(path):
        if is_api:
            return JSONResponse({"error": "setup_required"}, status_code=401)
        return RedirectResponse(root + "/setup", status_code=303)

    # Phase 2: session check. Public paths bypass this.
    if not _is_public(path):
        token = request.cookies.get("session")
        ctx = _session_ctx(token)
        if not ctx:
            if is_api:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return RedirectResponse(root + "/login", status_code=303)

        # Phase 3: forced password change. Block everything except the
        # change-password endpoints, whoami, and logout until it's done.
        if ctx.get("must_change_password") and not any(
            path == p or path.startswith(p + "/") for p in _FORCED_CHANGE_ALLOWED
        ):
            if is_api:
                return JSONResponse(
                    {"error": "password_change_required"}, status_code=409
                )
            return RedirectResponse(root + "/change-password", status_code=303)

    return await call_next(request)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/stats")
def api_stats(window: str = Query("24h")):
    from .topology import window_floor_or_none

    since = window_floor_or_none(window)
    entries = ledger.get_stats_rows(since=since)
    if not entries:
        return {
            "total": 0,
            "first_entry": None,
            "last_entry": None,
            "unique_agents": 0,
            "unique_sessions": 0,
            "action_types": {},
            "upstreams": {},
            "agents": {},
            "activity": {},
        }

    agents: dict = {}
    sessions: set = set()
    action_types: dict = {}
    upstreams: dict = {}
    activity: dict = {}

    for e in entries:
        agents[e["agent_id"]] = agents.get(e["agent_id"], 0) + 1
        sessions.add(e.get("session_id", ""))
        at = e["action_type"]
        action_types[at] = action_types.get(at, 0) + 1
        up = e.get("upstream", "") or "(none)"
        upstreams[up] = upstreams.get(up, 0) + 1
        dt = datetime.fromtimestamp(e["timestamp"])
        day_key = dt.strftime("%Y-%m-%d %H:00")
        activity[day_key] = activity.get(day_key, 0) + 1

    return {
        "total": len(entries),
        "first_entry": datetime.fromtimestamp(entries[0]["timestamp"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "last_entry": datetime.fromtimestamp(entries[-1]["timestamp"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "unique_agents": len(agents),
        "unique_sessions": len(sessions),
        "action_types": action_types,
        "upstreams": upstreams,
        "agents": agents,
        "activity": activity,
    }


@app.get("/api/tpm-status")
def api_tpm_status():
    """Return TPM mode status."""
    return {
        "tpm_available": _TPM_AVAILABLE,
        "mode": (
            ("TPM" if _TPM_AVAILABLE else "Software")
            if _features.HAS_SIGNING
            else "disabled"
        ),
        "signing_enabled": _features.HAS_SIGNING,
    }


@app.get("/api/dlp/health")
async def api_dlp_health():
    """Surface DLP scanner sidecar health for the compliance page.

    Built-in BERT + regex scanners ship with rules preloaded and run on
    every request — they cannot be disabled. The only failure mode is
    the sidecar being unreachable, which this endpoint surfaces so the
    UI can render PARTIAL only when scanning is actually degraded.
    """
    return await dlp.health_check()


# ---------------------------------------------------------------------------
# PDF export endpoints — each returns a self-verifying application/pdf
# stream (Cryptographic verification block embedded on the last page).
# ---------------------------------------------------------------------------


def _pdf_response(pdf_bytes: bytes, filename: str):
    from fastapi.responses import Response

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Kyde-Signed": "1",
        },
    )


def _csv_response(rows: list[list], headers: list[str], filename: str):
    """Build a streaming-style CSV response. Properly escapes fields with
    commas / newlines / quotes via the stdlib csv module so spreadsheets
    open it cleanly."""
    import csv
    import io
    from fastapi.responses import Response

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def _json_attachment(payload: dict, filename: str):
    """JSON response sent as an attachment — keeps the downloaded file's
    name predictable for compliance handoffs."""
    import json
    from fastapi.responses import Response

    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/export/audit-log-csv")
async def api_export_audit_log_csv(
    request: Request,
    session: str | None = Cookie(None),
):
    """CSV export of the audit log honoring the same filters as the PDF.
    Body: {action?, upstream?, q?, window?, limit?}. Returns text/csv with
    one row per ledger entry."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}

    from .topology import window_floor_or_none

    window = str(body.get("window") or "24h")
    since = window_floor_or_none(window)

    page = ledger.list_entries_paginated(
        limit=int(body.get("limit") or 5000),
        cursor=None,
        action=body.get("action") or None,
        upstream=body.get("upstream") or None,
        agent_id=body.get("agent_id") or None,
        search=body.get("q") or None,
        since=since,
    )
    headers = [
        "seq",
        "entry_id",
        "timestamp_iso",
        "agent_id",
        "action_type",
        "model",
        "upstream",
        "session_id",
        "prompt_tokens",
        "completion_tokens",
    ]
    rows = []
    for e in page["items"]:
        dt = datetime.fromtimestamp(e["timestamp"]).isoformat()
        rows.append(
            [
                e["seq"],
                e["entry_id"],
                dt,
                e["agent_id"],
                e["action_type"],
                e.get("model", ""),
                e.get("upstream", ""),
                e.get("session_id", ""),
                e.get("prompt_tokens", 0),
                e.get("completion_tokens", 0),
            ]
        )
    return _csv_response(rows, headers, "audit-log.csv")


@app.post("/api/export/ledger-csv")
async def api_export_ledger_csv(
    request: Request,
    session: str | None = Cookie(None),
):
    """Full ledger CSV dump for compliance handoff — metadata only,
    intentionally excluding message content. Includes the chain-integrity
    fields (prev_hash, entry_hash, signature) so the receiver has an
    auditable trail without the underlying prompts/responses.

    Body: {window?}. Default window = 24h. Use 'all' for a full archive.
    Open to admin or auditor (matches the Compliance page's route guard).
    """
    if not _is_admin_or_auditor(session):
        return JSONResponse(
            {"error": "admin or auditor role required"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}

    from .topology import window_floor_or_none

    window = str(body.get("window") or "24h")
    since = window_floor_or_none(window)

    # Pull every entry inside the window with chain fields. This goes
    # through a dedicated query because list_entries_paginated() caps at
    # 500 per page and we want one big dump.
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            if since is None:
                cur.execute(
                    "SELECT seq, entry_id, timestamp, agent_id, action_type,"
                    " model, upstream, session_id, input_hash, output_hash,"
                    " prev_hash, entry_hash, signature, prompt_tokens,"
                    " completion_tokens, client_ip, user_agent"
                    " FROM ledger ORDER BY seq ASC"
                )
            else:
                cur.execute(
                    "SELECT seq, entry_id, timestamp, agent_id, action_type,"
                    " model, upstream, session_id, input_hash, output_hash,"
                    " prev_hash, entry_hash, signature, prompt_tokens,"
                    " completion_tokens, client_ip, user_agent"
                    " FROM ledger WHERE timestamp >= %s ORDER BY seq ASC",
                    (since,),
                )
            entries = list(cur.fetchall())

    headers = [
        "seq",
        "entry_id",
        "timestamp_iso",
        "agent_id",
        "action_type",
        "model",
        "upstream",
        "session_id",
        "input_hash",
        "output_hash",
        "prev_hash",
        "entry_hash",
        "signature_b64",
        "prompt_tokens",
        "completion_tokens",
        "client_ip",
        "user_agent",
    ]
    rows = [
        [
            e["seq"],
            e["entry_id"],
            datetime.fromtimestamp(e["timestamp"]).isoformat(),
            e["agent_id"],
            e["action_type"],
            e.get("model", ""),
            e.get("upstream", ""),
            e.get("session_id", ""),
            e["input_hash"],
            e["output_hash"],
            e["prev_hash"],
            e["entry_hash"],
            e["signature"],
            e.get("prompt_tokens", 0),
            e.get("completion_tokens", 0),
            e.get("client_ip", ""),
            e.get("user_agent", ""),
        ]
        for e in entries
    ]
    return _csv_response(rows, headers, f"ledger-{window}.csv")


@app.post("/api/export/chain-signatures")
async def api_export_chain_signatures(
    request: Request,
    session: str | None = Cookie(None),
):
    """Chain-signature JSON export designed for offline verification.

    Each entry carries the `signable` payload (the exact dict that
    signing.canonical_bytes() was run over), the expected entry_hash, and
    the Ed25519/ECDSA signature. The recipient implements the same
    canonical_bytes() (sort_keys=True, separators=(",", ":")) and can
    verify every entry independently.

    The PEM-encoded public key + algorithm sit at the file root so the
    consumer doesn't need to call back to the server.

    Body: {window?}. Open to admin or auditor (matches the Compliance
    page's route guard). The exported `signable.why` carries message
    content — admins viewing this are doing a deliberate compliance
    handoff, same access pattern as the PDF report.
    """
    if not _is_admin_or_auditor(session):
        return JSONResponse(
            {"error": "admin or auditor role required"},
            status_code=403,
        )
    if not _features.HAS_SIGNING:
        return JSONResponse(
            {
                "error": (
                    "Chain-signature export requires independent audit signing, "
                    "an enterprise feature not enabled in this edition."
                )
            },
            status_code=404,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}

    from .topology import window_floor_or_none
    from . import signing as signing_mod
    import base64

    window = str(body.get("window") or "24h")
    since = window_floor_or_none(window)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            if since is None:
                cur.execute(
                    "SELECT entry_id, timestamp, agent_id, action_type,"
                    " model, why, input_hash, output_hash, tool_calls,"
                    " prev_hash, entry_hash, signature"
                    " FROM ledger ORDER BY seq ASC"
                )
            else:
                cur.execute(
                    "SELECT entry_id, timestamp, agent_id, action_type,"
                    " model, why, input_hash, output_hash, tool_calls,"
                    " prev_hash, entry_hash, signature"
                    " FROM ledger WHERE timestamp >= %s ORDER BY seq ASC",
                    (since,),
                )
            rows = list(cur.fetchall())

    entries = [
        {
            "entry_id": r["entry_id"],
            "signable": {
                "entry_id": r["entry_id"],
                "timestamp": r["timestamp"],
                "agent_id": r["agent_id"],
                "action_type": r["action_type"],
                "model": r["model"],
                "why": r["why"],
                "input_hash": r["input_hash"],
                "output_hash": r["output_hash"],
                "tool_calls": r["tool_calls"],
                "prev_hash": r["prev_hash"],
            },
            "entry_hash": r["entry_hash"],
            "signature_b64": r["signature"],
        }
        for r in rows
    ]

    # Read the public key as PEM and base64-wrap it so JSON tools that
    # struggle with embedded newlines stay happy.
    try:
        pub_pem = signing_mod.PUBLIC_KEY_PATH.read_bytes()
        pub_pem_b64 = base64.b64encode(pub_pem).decode()
    except FileNotFoundError:
        pub_pem_b64 = ""

    try:
        fingerprint = signing_mod.public_key_fingerprint()
    except FileNotFoundError:
        fingerprint = ""

    payload = {
        "schema_version": 1,
        "format": "chain-signatures-v1",
        "window": window,
        "exported_at": datetime.now().isoformat(),
        "entry_count": len(entries),
        "algorithm": "ECDSA-P256 (TPM)" if signing_mod._TPM_AVAILABLE else "Ed25519",
        "public_key_pem_b64": pub_pem_b64,
        "public_key_fingerprint": fingerprint,
        "canonical_bytes_note": (
            "Recipients reconstruct canonical_bytes for each signable via "
            "json.dumps(signable, sort_keys=True, separators=(',', ':'))."
            "encode('utf-8'). Verify SHA-256 against entry_hash and "
            "verify_payload against signature_b64."
        ),
        "entries": entries,
    }
    return _json_attachment(payload, f"chain-signatures-{window}.json")


@app.post("/api/export/compliance-report")
def api_export_compliance_report(session: str | None = Cookie(None)):
    """Render the Compliance hero state as a signed PDF."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import pdf_export

    if _features.HAS_SIGNING:
        try:
            from .signing import public_key_fingerprint

            fp = public_key_fingerprint()
        except FileNotFoundError:
            fp = "(no public key on file)"
    else:
        fp = "(signing disabled — sandbox edition)"
    valid, errors = ledger.verify_chain(record=False)
    total = ledger.count_entries()
    sig_failures = sum(1 for e in errors if "Invalid signature" in e)
    bundle = pdf_export.compliance_report(
        {
            "status": "COMPLIANT" if valid and sig_failures == 0 else "NON_COMPLIANT",
            "total_entries": total,
            "chain_intact": valid,
            "signature_failures": sig_failures,
            "signature_alg": (
                ("ECDSA-P256 (TPM)" if _TPM_AVAILABLE else "Ed25519")
                if _features.HAS_SIGNING
                else "unsigned"
            ),
            "public_key_fingerprint": fp,
            "regulatory_mappings": [
                {
                    "framework": "EU AI Act",
                    "articles": [
                        "Art. 9 — Risk management",
                        "Art. 12 — Record keeping",
                        "Art. 13 — Transparency",
                    ],
                },
                {
                    "framework": "DORA",
                    "articles": [
                        "Art. 8 — ICT risk management",
                        "Art. 10 — Detection",
                    ],
                },
                {
                    "framework": "NIS-2",
                    "articles": [
                        "Art. 21 — Security measures",
                        "Art. 23 — Incident reporting",
                    ],
                },
                {
                    "framework": "GDPR Art. 30",
                    "articles": [
                        "Records of processing",
                        "Data minimization evidence",
                    ],
                },
            ],
        }
    )
    return _pdf_response(bundle.pdf, "compliance-report.pdf")


@app.post("/api/export/audit-log")
async def api_export_audit_log(
    request: Request,
    session: str | None = Cookie(None),
):
    """Export filtered audit log entries as a signed PDF. Body mirrors the
    /api/entries query params (action, upstream, agent_id, q, limit)."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import pdf_export

    try:
        body = await request.json()
    except Exception:
        body = {}

    from .topology import window_floor_or_none

    window = str(body.get("window") or "24h")
    since = window_floor_or_none(window)

    page = ledger.list_entries_paginated(
        limit=int(body.get("limit") or 500),
        cursor=None,
        action=body.get("action") or None,
        upstream=body.get("upstream") or None,
        agent_id=body.get("agent_id") or None,
        search=body.get("q") or None,
        since=since,
    )
    decorated = [_decorate_entry(e) for e in page["items"]]
    bundle = pdf_export.audit_log(
        {
            "filters": {
                "action": body.get("action"),
                "upstream": body.get("upstream"),
                "agent_id": body.get("agent_id"),
                "q": body.get("q"),
                "window": window,
            },
            "entries": decorated,
            "total_count": page["total_count"],
        }
    )
    return _pdf_response(bundle.pdf, "audit-log.pdf")


@app.post("/api/export/compliance-evidence")
async def api_export_compliance_evidence(
    request: Request,
    session: str | None = Cookie(None),
):
    """Signed evidence snapshot for a single session or alert. Body:
    `{kind: 'session'|'alert', id: '<id>'}`."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import pdf_export

    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = (body or {}).get("kind")
    target = (body or {}).get("id")
    if kind not in ("session", "alert") or not target:
        return JSONResponse(
            {"error": "kind ('session'|'alert') and id required"}, status_code=400
        )

    if kind == "session":
        entries = ledger.get_session_detail(target, limit=500)
        decorated = [_decorate_entry(e) for e in entries]
        serial = ledger.get_session_serial_id(target)
        bundle = pdf_export.compliance_evidence(
            {
                "title": "Session evidence",
                "subject": f"Session {target} (SES-{serial or '—'})",
                "rows": [
                    ("Session ID", target),
                    ("Serial", f"SES-{serial}" if serial else "—"),
                    ("Entry count", len(entries)),
                ],
                "entries": decorated,
            }
        )
        return _pdf_response(bundle.pdf, f"session-{target}.pdf")

    # kind == "alert"
    rows = ledger.list_dlp_alerts(limit=1, status=None)
    alert = next((a for a in rows if a.get("alert_id") == target), None)
    if alert is None:
        return JSONResponse({"error": "alert not found"}, status_code=404)
    bundle = pdf_export.compliance_evidence(
        {
            "title": "Alert evidence",
            "subject": f"Alert {target}",
            "rows": [
                ("Alert ID", target),
                ("Serial", f"ALT-{alert.get('serial_id', alert.get('id'))}"),
                ("Severity", alert.get("severity", "—")),
                ("Score", alert.get("score", 0)),
                ("Status", alert.get("status", "—")),
                ("Disposition", alert.get("disposition", "—")),
                ("Entry ID", alert.get("entry_id", "—")),
                ("Session ID", alert.get("session_id", "—")),
                ("Created", alert.get("created_dt", "—")),
            ],
        }
    )
    return _pdf_response(bundle.pdf, f"alert-{target}.pdf")


@app.post("/api/export/incident-report")
async def api_export_incident_report(
    request: Request,
    session: str | None = Cookie(None),
):
    """Export an agent-chain incident report. Body:
      `{chain_label, status, incident_serial, steps:[{label, status, agent_id, dt}], notes}`.
    The frontend assembles the body from the current agent-chains state
    (mock or live)."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import pdf_export

    try:
        body = await request.json()
    except Exception:
        body = {}
    bundle = pdf_export.incident_report(
        {
            "chain_label": body.get("chain_label", "Untitled chain"),
            "status": body.get("status", "UNKNOWN"),
            "incident_serial": body.get("incident_serial", "—"),
            "steps": body.get("steps", []),
            "notes": body.get("notes", ""),
        }
    )
    return _pdf_response(bundle.pdf, "incident-report.pdf")


@app.get("/api/verification-runs")
def api_verification_runs(
    limit: int = Query(30, ge=1, le=200),
    session: str | None = Cookie(None),
):
    """Paginated chain-verification history. Auditors and admins only;
    everyone else gets a 401."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = ledger.list_verification_runs(limit=limit)
    out = []
    for r in rows:
        out.append(
            {
                "run_id": str(r["run_id"]),
                "run_at": r["run_at"].isoformat(),
                "total_entries": int(r["total_entries"]),
                "verified_entries": int(r["verified_entries"]),
                "chain_breaks": int(r["chain_breaks"]),
                "signature_failures": int(r["signature_failures"]),
                "first_broken_seq": (
                    int(r["first_broken_seq"])
                    if r["first_broken_seq"] is not None
                    else None
                ),
                "signature_alg": r["signature_alg"],
                "status": r["status"],
                "error_sample": r["error_sample"] or [],
            }
        )
    return out


@app.get("/api/verify")
def api_verify():
    try:
        valid, errors = ledger.verify_chain()
        count = ledger.count_entries()
        chain_breaks = sum(1 for e in errors if "Chain break" in e)
        sig_failures = sum(1 for e in errors if "Invalid signature" in e)
        try:
            fp = (
                public_key_fingerprint()
                if _features.HAS_SIGNING
                else "(signing disabled)"
            )
        except FileNotFoundError:
            fp = "(no public key found)"
        # ITIL Phase 1: sync incidents from chain errors
        _sync_chain_incidents(errors)
        return {
            "valid": valid,
            "errors": errors,
            "entry_count": count,
            "chain_breaks": chain_breaks,
            "signature_failures": sig_failures,
            "fingerprint": fp,
        }
    except Exception as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "entry_count": 0,
            "chain_breaks": 0,
            "signature_failures": 0,
            "fingerprint": "",
        }


def _decorate_entry(e: dict, *, auditor: bool = False) -> dict:
    """Attach the derived fields the frontend expects on a ledger row.

    When `auditor=True`, also attach `why_preview` — a short excerpt of the
    first user/system + last message so the Audit Log can show a hover
    preview without a per-row /api/entry fetch. Non-auditors get
    why_preview='' so the role gate matches the session-detail behavior.
    """
    tool_calls = e.get("tool_calls") or []
    e["tool_calls_parsed"] = tool_calls
    e["tool_count"] = len(tool_calls)
    e["first_tool"] = tool_calls[0].get("function", "?") if tool_calls else "-"
    e["dt"] = datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
    e["prompt_tokens"] = e.get("prompt_tokens", 0) or 0
    e["completion_tokens"] = e.get("completion_tokens", 0) or 0
    # Backfilled rows from before migration 0010 may have NULL request_kind
    # in DB; surface as 'unknown' so the frontend never has to handle null.
    e["request_kind"] = e.get("request_kind") or "unknown"
    if auditor:
        why = e.get("why") or []
        if why:
            first = why[0]
            last = why[-1]
            first_str = (
                f"[{first.get('role', '?')}] {str(first.get('content', ''))[:200]}"
            )
            last_str = f"[{last.get('role', '?')}] {str(last.get('content', ''))[:200]}"
            e["why_preview"] = (
                first_str if len(why) == 1 else f"{first_str}\n…\n{last_str}"
            )
        else:
            e["why_preview"] = ""
    else:
        e["why_preview"] = ""
    return e


@app.get("/api/entries")
def api_entries(
    limit: int = Query(50, ge=1, le=500),
    cursor: int | None = Query(None),
    action: str | None = Query(None),
    upstream: str | None = Query(None),
    agent_id: str | None = Query(None),
    session_id: str | None = Query(None),
    q: str | None = Query(None, description="Case-insensitive substring search"),
    window: str = Query("24h"),
    session: str | None = Cookie(None),
):
    from .topology import window_floor_or_none

    since = window_floor_or_none(window)
    auditor = _is_auditor(session)
    page = ledger.list_entries_paginated(
        limit=limit,
        cursor=cursor,
        action=action or None,
        upstream=upstream or None,
        agent_id=agent_id or None,
        session_id=session_id or None,
        search=q or None,
        since=since,
    )
    return {
        "items": [_decorate_entry(e, auditor=auditor) for e in page["items"]],
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
        "total_count": page["total_count"],
    }


@app.get("/api/entries/facets")
def api_entries_facets():
    """Distinct action_type + upstream values for the Timeline filter dropdowns."""
    return ledger.entry_facets()


@app.get("/api/entry/{entry_ref}")
def api_entry(entry_ref: str, session: str | None = Cookie(None)):
    e = ledger.get_entry_by_ref(entry_ref)
    if not e:
        return JSONResponse({"error": "not found"}, status_code=404)

    # JSONB columns come back pre-parsed; keep the legacy *_parsed keys that
    # the frontend already expects.
    e["why_parsed"] = e.get("why") or []
    e["tool_calls_parsed"] = e.get("tool_calls") or []
    e["full_messages_parsed"] = e.get("full_messages") or []
    e["dt"] = datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
    e["prompt_tokens"] = e.get("prompt_tokens", 0) or 0
    e["completion_tokens"] = e.get("completion_tokens", 0) or 0

    signable = {
        "entry_id": e["entry_id"],
        "timestamp": e["timestamp"],
        "agent_id": e["agent_id"],
        "action_type": e["action_type"],
        "model": e["model"],
        "why": e["why_parsed"],
        "input_hash": e["input_hash"],
        "output_hash": e["output_hash"],
        "tool_calls": e["tool_calls_parsed"],
        "prev_hash": e["prev_hash"],
    }
    if _features.HAS_SIGNING and e.get("signature"):
        try:
            e["signature_valid"] = verify_payload(signable, e["signature"])
        except Exception:
            e["signature_valid"] = False
    else:
        # Sandbox edition / unsigned entry: chain-verified only.
        e["signature_valid"] = None

    e["total_entries"] = ledger.count_entries()

    # Role gate: only auditors see captured message content. The signature is
    # verified above against the full payload, so redaction happens last.
    auditor = _is_auditor(session)
    if not auditor:
        e["why_parsed"] = []
        e["full_messages_parsed"] = []
        e.pop("why", None)
        e.pop("full_messages", None)
        e["content_redacted"] = True
    else:
        e["content_redacted"] = False

    # Look up the cached hostname for client_ip so the dialog can render
    # "hostname (ip)" without a second round-trip. Hits host_resolutions
    # only — no on-demand DNS — so this stays O(1) and free of latency
    # spikes when the client IP isn't cached yet.
    client_ip = e.get("client_ip")
    if client_ip:
        host_row = ledger.get_host_resolution(client_ip)
        e["client_hostname"] = (host_row or {}).get("hostname")
    else:
        e["client_hostname"] = None

    # Attach any DLP alerts raised on this entry so the dialog can render
    # the Alerts section on the Metadata tab and highlight matching
    # messages on the Messages tab. Same auditor-redaction the list
    # endpoint applies.
    entry_alerts = ledger.get_dlp_alerts_for_entry(e["entry_id"])
    for alert in entry_alerts:
        _apply_dlp_redaction(alert, auditor)
    e["dlp_alerts"] = entry_alerts

    # Where this entry's contribution begins inside full_messages — i.e.
    # the index of the first message that wasn't already in the prior
    # entry's full_messages array. Drives the "This turn" tab on the
    # entry-detail dialog and the highlight cursor on "Full context".
    # 0 means the entry is the first call in its session, so everything
    # in full_messages is new.
    e["new_message_offset"] = ledger.get_prior_full_messages_length(
        e.get("session_id") or "", e["seq"]
    )
    return e


def _bucket_init() -> dict:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "requests": 0,
    }


@app.get("/api/token-analysis")
def api_token_analysis(
    window: str = Query("24h"),
    agent_id: str | None = Query(None),
):
    """Aggregate prompt/completion tokens by agent, upstream, model, and hour.

    `window` (default 24h) restricts which ledger rows feed the aggregates.
    Accepts 1h / 24h / 7d / 30d / 90d / all.

    `agent_id` (optional) scopes the entire aggregation to one agent —
    the Agent detail page's per-model token breakdown sets it.
    """
    from .topology import window_floor_or_none

    since = window_floor_or_none(window)
    rows = ledger.get_token_analysis_rows(since=since, agent_id=agent_id or None)

    by_agent: dict[str, dict] = {}
    by_upstream: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_hour: dict[str, dict] = {}
    total_up = 0
    total_down = 0

    for r in rows:
        up = r["prompt_tokens"] or 0
        down = r["completion_tokens"] or 0

        total_up += up
        total_down += down

        for key, bucket_map in (
            (r["agent_id"], by_agent),
            (r["upstream"] or "(none)", by_upstream),
            (r["model"] or "unknown", by_model),
        ):
            if key not in bucket_map:
                bucket_map[key] = _bucket_init()
            b = bucket_map[key]
            b["prompt_tokens"] += up
            b["completion_tokens"] += down
            b["requests"] += 1

        dt = datetime.fromtimestamp(r["timestamp"])
        hour_key = dt.strftime("%Y-%m-%d %H:00")
        if hour_key not in by_hour:
            by_hour[hour_key] = _bucket_init()
        h = by_hour[hour_key]
        h["prompt_tokens"] += up
        h["completion_tokens"] += down
        h["requests"] += 1

    for bucket_map in (by_agent, by_upstream, by_model, by_hour):
        for k in bucket_map:
            bucket_map[k]["total_tokens"] = (
                bucket_map[k]["prompt_tokens"] + bucket_map[k]["completion_tokens"]
            )

    return {
        "total_prompt_tokens": total_up,
        "total_completion_tokens": total_down,
        "total_tokens": total_up + total_down,
        "by_agent": by_agent,
        "by_upstream": by_upstream,
        "by_model": by_model,
        "by_hour": by_hour,
    }


@app.get("/api/fleet-trust")
def api_fleet_trust(
    window: str = Query("24h"),
    session: str | None = Cookie(None),
):
    """Fleet & per-agent trust score over the window.

    Computes the 5-dimension formula (see `trust.py`) from existing
    signals — DLP alerts, policy blocks, tool patterns, request outcomes,
    token efficiency, and chain verification. `window` accepts
    1h / 24h / 7d / 30d / 90d / all. The score renders even in the sandbox
    edition (signing off): Compliance falls back to an audit-trail baseline.
    """
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from .topology import window_floor_or_none
    from . import trust

    since = window_floor_or_none(window)
    return trust.fleet_trust(since, signing_enabled=_features.HAS_SIGNING)


@app.get("/api/sessions")
def api_sessions(
    limit: int = Query(50, ge=1, le=500),
    cursor: float | None = Query(None),
    window: str = Query("24h"),
    has_alert: str | None = Query(None, pattern="^(yes|no)$"),
    agent: list[str] | None = Query(None),
    sort: str = Query("newest", pattern="^(newest|oldest|entries|agents)$"),
    status: list[str] | None = Query(None),
):
    """Paginated session summaries (one row per session_id).

    Filters: `window` (1h/24h/7d/30d/90d/all, default 24h), `has_alert`
    (yes|no — open alerts only), `agent` (repeatable, multi-select),
    `sort` (newest|oldest|entries|agents), and `status` (subset of
    {blocked, observed, allowed} — repeatable). Status is derived:
      blocked  = session contains an action_type='policy_block' entry
      observed = no block but at least one open DLP alert
      allowed  = neither

    Cursor pagination is keyed on the session's `last_time` (epoch
    seconds). Cursor semantics are well-defined for sort=newest; for
    other sorts the cursor still works but page boundaries may not align
    perfectly with the global ORDER BY.
    """
    from .topology import window_floor_or_none

    since = window_floor_or_none(window)

    try:
        page = ledger.list_session_summaries(
            limit=limit,
            cursor=cursor,
            since=since,
            has_alert=has_alert,
            agents=agent or None,
            sort=sort,
            status=status or None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    from . import intent_classifier

    sids = [s["session_id"] for s in page["items"]]
    intents = intent_classifier.get_intents_for(sids)

    items = []
    for s in page["items"]:
        intent = intents.get(s["session_id"])
        # Derive the BLOCKED/OBSERVED/ALLOWED status from the aggregates the
        # SQL already computes. The agent-chains page renders the chain
        # outcome from this field; same field powers the status filter.
        if s.get("has_block"):
            session_status = "blocked"
        elif s.get("has_open_alert"):
            session_status = "observed"
        else:
            session_status = "allowed"
        items.append(
            {
                "session_id": s["session_id"],
                "serial_id": s.get("serial_id"),
                "entry_count": s["entry_count"],
                "agent_count": s["agent_count"],
                "agents": list(s["agents"] or []),
                "first_time": datetime.fromtimestamp(s["first_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "last_time": datetime.fromtimestamp(s["last_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "last_timestamp": s["last_time"],
                "first_timestamp": s["first_time"],
                "duration_seconds": float(s["last_time"]) - float(s["first_time"]),
                "intent": intent["intent"] if intent else None,
                "intent_confidence": intent["confidence"] if intent else None,
                "status": session_status,
                "has_block": bool(s.get("has_block")),
                "has_open_alert": bool(s.get("has_open_alert")),
            }
        )
    return {
        "items": items,
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
    }


@app.post("/api/sessions/{session_id}/classify")
def api_session_classify(session_id: str, session: str | None = Cookie(None)):
    """Run the LLM-backed intent classifier for one session and cache the
    result. Returns 503 if no classifier is configured (set the
    INTENT_CLASSIFIER_URL env var)."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import intent_classifier

    result = intent_classifier.classify_session(session_id)
    if result is None:
        return JSONResponse(
            {"error": "classifier not configured or no usable history"},
            status_code=503,
        )
    return result


@app.get("/api/sessions/{session_id}")
def api_session_detail(
    session_id: str,
    limit: int = Query(500, ge=1, le=2000),
    session: str | None = Cookie(None),
):
    """Entries for a single session. `why` content is gated behind the
    auditor role — the same rule as the entry-detail endpoint.
    """
    auditor = _is_auditor(session)
    rows = ledger.get_session_detail(session_id, limit=limit)
    serial_id = ledger.get_session_serial_id(session_id)

    # One round-trip for all DLP alerts in this session, indexed by entry_id
    # so we can attach the per-entry list in O(n) below. The Sessions screen
    # renders a red banner per entry that has any alerts.
    alerts_by_entry = ledger.get_dlp_alerts_by_session(session_id)

    entries = []
    distinct_ips: list[str] = []
    seen_ips: set[str] = set()
    for e in rows:
        tc = e.get("tool_calls") or []
        entry_alerts = alerts_by_entry.get(e["entry_id"], [])
        entry = {
            "seq": e["seq"],
            "entry_id": e["entry_id"],
            "dt": datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": e["timestamp"],
            "action_type": e["action_type"],
            "model": e["model"],
            "agent_id": e["agent_id"],
            "tool_calls": tc,
            "tool_count": len(tc),
            "first_tool": tc[0].get("function", "?") if tc else "-",
            "why_last": "",
            "upstream": e.get("upstream", ""),
            "dlp_alerts": entry_alerts,
            "prompt_tokens": e.get("prompt_tokens", 0) or 0,
            "completion_tokens": e.get("completion_tokens", 0) or 0,
            # NULL on rows from before migration 0010 — present as 'unknown'.
            "request_kind": e.get("request_kind") or "unknown",
        }
        ip = (e.get("client_ip") or "").strip()
        if ip and ip not in seen_ips:
            seen_ips.add(ip)
            distinct_ips.append(ip)
        if auditor:
            why = e.get("why") or []
            if why:
                last = why[-1]
                entry["why_last"] = (
                    f"[{last.get('role', '?')}] "
                    f"{str(last.get('content', ''))[:200]}"
                )
        entries.append(entry)

    # Distinct client IPs across the session, each annotated with the
    # cached hostname (host_resolutions hit only — no on-demand DNS) so
    # the UI can render a per-host link without a second round-trip. In
    # practice most sessions have a single IP, but NAT churn and mobile
    # roaming mean the field is a list, not a scalar.
    hosts = []
    for ip in distinct_ips:
        row = ledger.get_host_resolution(ip)
        hosts.append({"ip": ip, "hostname": (row or {}).get("hostname")})

    return {
        "session_id": session_id,
        "serial_id": serial_id,
        "entries": entries,
        "hosts": hosts,
        "content_redacted": not auditor,
    }


# ---------------------------------------------------------------------------
# ITIL Phase 1: Incidents, Metrics, Configuration
# ---------------------------------------------------------------------------


@app.get("/api/incidents")
def api_incidents(status: str = Query("")):
    """Return list of incidents with optional status filter."""
    result = list(INCIDENT_STORE)
    if status:
        result = [i for i in result if i["status"] == status]
    return result


_DLP_REDACTED_FIELDS = ("matched_value", "context_snippet")
_DLP_REDACTION_PLACEHOLDER = "<redacted — auditor role required>"


def _apply_dlp_redaction(alert: dict, auditor: bool) -> None:
    """In-place: mask sensitive finding fields for non-auditor callers.
    Mirrors what api_dlp_alerts has always done; extracted so single-alert
    fetches share the same gating rules."""
    if not auditor:
        for finding in alert.get("findings_parsed", []):
            if isinstance(finding, dict):
                for field in _DLP_REDACTED_FIELDS:
                    if field in finding:
                        finding[field] = _DLP_REDACTION_PLACEHOLDER
        # Drop the raw findings JSON — it duplicates the parsed form and
        # would otherwise leak the unredacted values.
        alert.pop("findings", None)
        alert["content_redacted"] = True
    else:
        alert["content_redacted"] = False


@app.get("/api/dlp-alerts")
def api_dlp_alerts(
    status: str = Query(""),
    source_type: str = Query(""),
    limit: int = Query(200),
    session: str | None = Cookie(None),
):
    """Return DLP alerts, newest first. Optional status / source filter.

    `source_type` accepts "chat" or "mcp" — splits the triage feed so the
    UI can render a Source pill without a second query path. Empty
    (default) returns both sources.
    """
    try:
        alerts = ledger.list_dlp_alerts(
            limit=limit,
            status=status if status else None,
            source_type=source_type if source_type else None,
        )
        auditor = _is_auditor(session)
        for alert in alerts:
            _apply_dlp_redaction(alert, auditor)
        return alerts
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/dlp-alerts/{alert_id}")
def api_dlp_alert_get(alert_id: str, session: str | None = Cookie(None)):
    """Return one DLP alert by alert_id, with the same auditor redaction
    rules as the list endpoint. Returns 404 if the id is unknown."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    alert = ledger.get_dlp_alert(alert_id)
    if not alert:
        return JSONResponse({"error": "not found"}, status_code=404)
    _apply_dlp_redaction(alert, _is_auditor(session))
    return alert


# ---------------------------------------------------------------------------
# DLP alert triage — lifecycle transitions + audit trail.
# See dlp_triage.py for the transition matrix. Session roles are mapped here:
# `admin` → lead (can escalate, reopen), every other authenticated user →
# analyst. Unauthenticated requests are rejected.
# ---------------------------------------------------------------------------


@app.post("/api/dlp-alerts/{alert_id}/transition")
async def api_dlp_alert_transition(
    alert_id: str,
    request: Request,
    session: str | None = Cookie(None),
):
    ctx = _session_ctx(session)
    if not ctx:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    to_status = str(body.get("to_status") or "")
    if not to_status:
        return JSONResponse({"error": "to_status is required"}, status_code=400)

    disposition = body.get("disposition")
    assignee_id = body.get("assignee_id")
    note = str(body.get("note") or "")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else None

    try:
        row = dlp_triage.transition(
            alert_id=alert_id,
            to_status=to_status,
            actor_kind="user",
            actor_id=ctx["user_id"],
            disposition=disposition,
            assignee_id=int(assignee_id) if assignee_id is not None else None,
            note=note,
            metadata=metadata,
        )
    except dlp_triage.TransitionError as exc:
        msg = str(exc)
        if "not found" in msg:
            return JSONResponse({"error": msg}, status_code=404)
        return JSONResponse({"error": msg}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return row


@app.get("/api/dlp-alerts/{alert_id}/events")
def api_dlp_alert_events(alert_id: str, session: str | None = Cookie(None)):
    if not _is_viewer(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        return dlp_triage.list_events(alert_id)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/metrics")
def api_metrics():
    """Return KPI metrics for SLA monitoring and dashboards."""
    now = time.time()
    cutoff_24h = now - 86400
    cutoff_1h = now - 3600

    rows = ledger.get_metrics_rows()

    total = len(rows)
    count_24h = sum(1 for r in rows if r["timestamp"] >= cutoff_24h)
    count_1h = sum(1 for r in rows if r["timestamp"] >= cutoff_1h)
    tool_call_count = sum(1 for r in rows if r["action_type"] == "tool_call")

    valid, errors = ledger.verify_chain()
    sig_failures = sum(1 for e in errors if "Invalid signature" in e)
    chain_breaks = sum(1 for e in errors if "Chain break" in e)
    sig_success_rate = (total - sig_failures) / total if total else 1.0

    return {
        "total_entries": total,
        "entries_per_hour_24h": round(count_24h / 24, 2),
        "entries_per_hour_1h": float(count_1h),
        "signature_success_rate": round(sig_success_rate, 4),
        "tool_call_ratio": round(tool_call_count / total, 4) if total else 0.0,
        "chain_integrity": {"valid": valid, "break_count": chain_breaks},
        "signing_mode": (
            ("TPM" if _TPM_AVAILABLE else "Software")
            if _features.HAS_SIGNING
            else "disabled"
        ),
        "ledger_size_bytes": ledger.database_size_bytes(),
        "service_start_time": datetime.utcfromtimestamp(SERVICE_START_TIME).isoformat()
        + "Z",
        "uptime_seconds": round(now - SERVICE_START_TIME, 1),
    }


@app.get("/api/configuration")
def api_configuration():
    """Return current service configuration snapshot."""
    from . import server as _server

    if _features.HAS_SIGNING:
        from .signing import get_configuration_info

        cfg = get_configuration_info()
    else:
        cfg = {"signing_mode": "disabled", "tpm_available": False}
    # Upstream registry: the admin UI renders each entry's base URL + prefix
    # so operators can confirm the routing table at a glance. Sorted by
    # name for a stable order.
    upstreams = [
        {
            "name": name,
            "base": entry.get("base", ""),
            "api_prefix": entry.get("api_prefix", ""),
        }
        for name, entry in sorted(_server.UPSTREAMS.items())
    ]
    return {
        **cfg,
        "edition": _features.edition(),
        "signing_enabled": _features.HAS_SIGNING,
        "enforcement_enabled": _features.HAS_ENFORCEMENT,
        "configured_upstreams": upstreams,
        "ledger_backend": "postgres",
        "ledger_entry_count": ledger.count_entries(),
        "service_version": "0.1.0",
    }


@app.get("/api/telemetry/preview")
def api_telemetry_preview(session: str | None = Cookie(None)):
    """Return the exact telemetry batch that WOULD be sent right now.

    Admin-gated transparency surface: an operator can audit precisely what
    leaves the VPC before enabling delivery. Builds the payload for the
    window since the last successful send but sends nothing.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    state = ledger.get_telemetry_state()
    last_sent = float(state.get("last_sent") or 0.0)
    batch = telemetry.build_payload(last_sent, time.time())
    return {
        "enabled": bool(settings_module.get("TELEMETRY_ENABLED")),
        "endpoint_set": bool(settings_module.get("TELEMETRY_ENDPOINT")),
        "last_status": state.get("last_status", ""),
        "last_error": state.get("last_error", ""),
        "batch": batch,
    }


@app.post("/api/telemetry/send-now")
async def api_telemetry_send_now(session: str | None = Cookie(None)):
    """Run one emit cycle immediately (respects the configured endpoint).

    Admin-gated. Ignores the interval gate but still requires an endpoint to
    actually deliver; advances the watermark only on success.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    result = await telemetry.emit_once()
    return result


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

_AUTH_PAGE_STYLE = """<style>
:root {
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
[data-theme="dark"] {
  --bg-body: #0b0f14;
  --bg-card: rgba(24, 24, 27, 0.5);
  --bg-surface: #18181b;
  --bg-input: #09090b;
  --text-primary: #e6eaf0;
  --text-secondary: #a1a1aa;
  --text-muted: #71717a;
  --text-faint: #52525b;
  --border: #27272a;
  --border-hover: #3f3f46;
  --accent-blue: #4da3ff;
  --accent-red: #f87171;
  --btn-bg: #e6eaf0;
  --btn-text: #0b0f14;
  --btn-hover: #d4d4d8;
  --grid-color: #4da3ff0a;
}
[data-theme="light"] {
  --bg-body: #f8f9fa;
  --bg-card: rgba(255, 255, 255, 0.8);
  --bg-surface: #ffffff;
  --bg-input: #f4f4f5;
  --text-primary: #18181b;
  --text-secondary: #52525b;
  --text-muted: #71717a;
  --text-faint: #a1a1aa;
  --border: #e4e4e7;
  --border-hover: #d4d4d8;
  --accent-blue: #2563eb;
  --accent-red: #dc2626;
  --btn-bg: #18181b;
  --btn-text: #f4f4f5;
  --btn-hover: #27272a;
  --grid-color: #2563eb08;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  background: var(--bg-body);
  color: var(--text-primary);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  -webkit-font-smoothing: antialiased;
}
::selection { background: var(--accent-blue); color: var(--bg-body); }

.login-container {
  width: 100%;
  max-width: 380px;
  padding: 0 20px;
}

.login-logo {
  display: block;
  height: 32px;
  width: auto;
  margin-bottom: 32px;
}

.login-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 32px;
}

.login-title {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.025em;
  margin-bottom: 4px;
}

.login-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 24px;
}

.field-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-muted);
  margin-bottom: 6px;
  font-family: var(--font-mono);
}

.field-input {
  width: 100%;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
  font-size: 14px;
  padding: 10px 14px;
  font-family: var(--font-sans);
  outline: none;
  transition: border-color 0.15s cubic-bezier(.4,0,.2,1);
  margin-bottom: 20px;
}
.field-input:focus { border-color: var(--accent-blue); }

.login-btn {
  width: 100%;
  padding: 10px 14px;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-sans);
  border: none;
  border-radius: 0;
  cursor: pointer;
  background: var(--btn-bg);
  color: var(--btn-text);
  transition: background 0.15s cubic-bezier(.4,0,.2,1);
}
.login-btn:hover { background: var(--btn-hover); }

.login-error {
  padding: 10px 14px;
  border: 1px solid var(--accent-red);
  background: rgba(248, 113, 113, 0.08);
  border-radius: 4px;
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--accent-red);
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 11px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  letter-spacing: 0.05em;
}

/* Background grid (kyde.com style) */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(var(--grid-color) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-color) 1px, transparent 1px);
  background-size: 96px 96px;
  mask-image: radial-gradient(80% 60% at 50% 40%, #000 40%, transparent);
  -webkit-mask-image: radial-gradient(80% 60% at 50% 40%, #000 40%, transparent);
  pointer-events: none;
  z-index: 0;
}

.login-container { position: relative; z-index: 1; }

.policy-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin: -12px 0 16px;
  line-height: 1.5;
  font-family: var(--font-mono);
}
.policy-hint ul { list-style: none; padding: 0; margin: 4px 0 0; }
.policy-hint li { padding-left: 12px; position: relative; }
.policy-hint li::before { content: '·'; position: absolute; left: 0; color: var(--text-faint); }
.policy-hint li.ok { color: var(--accent-blue); }
.policy-hint li.ok::before { content: '✓'; color: var(--accent-blue); }
</style>"""


def _auth_page(
    title: str, subtitle: str, form_body: str, extra_script: str = ""
) -> str:
    """Render a consistently-styled standalone page (login / setup / change-password)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<script>document.documentElement.setAttribute('data-theme', localStorage.getItem('ledger-theme') || 'dark')</script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — KYDE Gateway</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300..900&display=swap" rel="stylesheet">
{_AUTH_PAGE_STYLE}
</head>
<body>
<div class="login-container">
  <img src="/logo.png" alt="KYDE" class="login-logo" />
  <div class="login-card">
    <div class="login-title">{title}</div>
    <div class="login-desc">{subtitle}</div>
    <div id="auth-error-slot"></div>
    {form_body}
  </div>
  <div class="login-footer">KYDE Gateway &middot; Agent Behavioral Ledger</div>
</div>
<script>
  (function() {{
    const err = new URLSearchParams(window.location.search).get('error');
    if (err) {{
      const slot = document.getElementById('auth-error-slot');
      slot.innerHTML = '<div class="login-error">' + err.replace(/</g,'&lt;') + '</div>';
    }}
  }})();
{extra_script}
</script>
</body>
</html>"""


LOGIN_HTML = _auth_page(
    title="Sign in",
    subtitle="Enter your credentials to access the audit dashboard.",
    form_body="""
    <form method="POST" action="login">
      <label class="field-label" for="username">Username</label>
      <input class="field-input" type="text" id="username" name="username" autofocus required autocomplete="username" placeholder="Enter username">
      <label class="field-label" for="password" style="margin-top:12px">Password</label>
      <input class="field-input" type="password" id="password" name="password" required autocomplete="current-password" placeholder="Enter password">
      <button class="login-btn" type="submit" style="margin-top:16px">Sign in</button>
    </form>""",
)


# Client-side policy hint script shared by setup + change-password forms.
# It is purely UX — the server always re-validates. Mirrors
# kyde.auth.validate_password.
_PASSWORD_POLICY_JS = r"""
  function _checkPolicy(pw) {
    return {
      len:    pw.length >= 12,
      upper:  /[A-Z]/.test(pw),
      lower:  /[a-z]/.test(pw),
      digit:  /[0-9]/.test(pw),
      special:/[!@#$%^&*()\-_=+\[\]{};:,.<>?\/\\|`~'"]/.test(pw),
    };
  }
  function _wireHint(inputId, hintId) {
    const input = document.getElementById(inputId);
    const hint  = document.getElementById(hintId);
    if (!input || !hint) return;
    const li = name => hint.querySelector('li[data-rule="' + name + '"]');
    input.addEventListener('input', () => {
      const r = _checkPolicy(input.value);
      Object.entries(r).forEach(([k, ok]) => {
        const el = li(k); if (el) el.classList.toggle('ok', ok);
      });
    });
  }
"""


_PASSWORD_POLICY_HINT = """
<div class="policy-hint">
  Password must contain:
  <ul>
    <li data-rule="len">at least 12 characters</li>
    <li data-rule="upper">one uppercase letter</li>
    <li data-rule="lower">one lowercase letter</li>
    <li data-rule="digit">one digit</li>
    <li data-rule="special">one special character</li>
  </ul>
</div>"""


SETUP_HTML = _auth_page(
    title="Create admin account",
    subtitle="No admin exists yet. Set up the admin credentials to get started.",
    form_body=f"""
    <form method="POST" action="setup">
      <label class="field-label" for="username">Username</label>
      <input class="field-input" type="text" id="username" name="username" value="admin" readonly autocomplete="off">
      <label class="field-label" for="email">Email</label>
      <input class="field-input" type="email" id="email" name="email" autofocus required autocomplete="email" placeholder="you@example.com">
      <label class="field-label" for="password">Password</label>
      <input class="field-input" type="password" id="password" name="password" required autocomplete="new-password" placeholder="Choose a strong password">
      {_PASSWORD_POLICY_HINT.replace('id="', 'id="setup-')}
      <label class="field-label" for="confirm">Confirm password</label>
      <input class="field-input" type="password" id="confirm" name="confirm" required autocomplete="new-password" placeholder="Repeat the password">
      <button class="login-btn" type="submit" style="margin-top:16px">Create admin</button>
    </form>""",
    extra_script=_PASSWORD_POLICY_JS
    + "\n  _wireHint('password', 'setup-policy-hint');",
)


CHANGE_PASSWORD_HTML_TEMPLATE = """
    <form id="cp-form">
      __CURRENT_BLOCK__
      <label class="field-label" for="new_password">New password</label>
      <input class="field-input" type="password" id="new_password" name="new_password" autofocus required autocomplete="new-password" placeholder="Choose a new password">
      __POLICY_HINT__
      <label class="field-label" for="confirm">Confirm new password</label>
      <input class="field-input" type="password" id="confirm" name="confirm" required autocomplete="new-password" placeholder="Repeat the new password">
      <button class="login-btn" type="submit" style="margin-top:16px">Change password</button>
    </form>"""


def _render_change_password_page(force: bool) -> str:
    current_block = (
        ""
        if force
        else (
            '<label class="field-label" for="current_password">Current password</label>'
            '<input class="field-input" type="password" id="current_password" name="current_password" required autocomplete="current-password" placeholder="Enter current password">'
        )
    )
    body = CHANGE_PASSWORD_HTML_TEMPLATE.replace(
        "__CURRENT_BLOCK__", current_block
    ).replace("__POLICY_HINT__", _PASSWORD_POLICY_HINT.replace('id="', 'id="cp-'))
    subtitle = (
        "You must choose a new password before continuing."
        if force
        else "Update the password on your account."
    )
    script = (
        _PASSWORD_POLICY_JS
        + "\n  _wireHint('new_password', 'cp-policy-hint');"
        + """
  document.getElementById('cp-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const slot = document.getElementById('auth-error-slot');
    slot.innerHTML = '';
    const currentEl = document.getElementById('current_password');
    const newPw = document.getElementById('new_password').value;
    const conf = document.getElementById('confirm').value;
    if (newPw !== conf) {
      slot.innerHTML = '<div class="login-error">Passwords do not match.</div>';
      return;
    }
    const body = { new_password: newPw };
    if (currentEl) body.current_password = currentEl.value;
    const resp = await fetch('api/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (resp.status === 204 || resp.ok) {
      window.location.href = './';
      return;
    }
    let msg = 'Password change failed.';
    try { const j = await resp.json(); if (j.error) msg = j.error; } catch (_) {}
    slot.innerHTML = '<div class="login-error">' + msg.replace(/</g,'&lt;') + '</div>';
  });
"""
    )
    return _auth_page(
        title="Change password",
        subtitle=subtitle,
        form_body=body,
        extra_script=script,
    )


@app.get("/api/whoami")
def api_whoami(session: str | None = Cookie(None)):
    """Return the current user's identity + roles + must_change_password flag."""
    ctx = _session_ctx(session)
    if not ctx:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {
        "username": ctx["username"],
        "roles": list(ctx["roles"]),
        "must_change_password": bool(ctx.get("must_change_password")),
    }


def _mint_session(user: dict) -> str:
    token = secrets.token_hex(32)
    ledger.create_session(
        token=token,
        user_id=user["id"],
        username=user["username"],
        roles=list(user["roles"]),
        must_change_password=bool(user["must_change_password"]),
    )
    return token


def _set_session_cookie(resp, token: str) -> None:
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=86400)


# Precomputed "dummy" hash used when a login attempt references a nonexistent
# username. Verifying against this keeps the timing profile identical to a real
# wrong-password attempt, so an attacker can't tell which usernames exist.
_DUMMY_HASH = auth.hash_password("timing-equalizer-placeholder")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN_HTML


@app.post("/login")
async def login_submit(request: Request):
    root = request.scope.get("root_path", "")
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password", "")

    user = ledger.get_user_by_username(username) if username else None
    hashed = ledger.get_password_hash(user["id"]) if user else None

    # Verify in constant time regardless of whether the user exists, disabled,
    # deleted, or locked — we never leak which condition failed.
    if auth.verify_password(password, hashed or _DUMMY_HASH) and user:
        if user["deleted"] or not user["enabled"] or user["locked"]:
            # Password was right, but the account is not usable. Same generic
            # message so we don't confirm credential validity to a locked-out
            # attacker.
            return RedirectResponse(
                root + "/login?error=Invalid+credentials", status_code=303
            )
        ledger.record_login_success(user["id"])
        token = _mint_session(user)
        target = "/change-password" if user["must_change_password"] else "/"
        resp = RedirectResponse(root + target, status_code=303)
        _set_session_cookie(resp, token)
        return resp

    if user:
        ledger.record_login_failure(user["id"])
    return RedirectResponse(root + "/login?error=Invalid+credentials", status_code=303)


@app.get("/logout")
def logout(request: Request, session: str | None = Cookie(None)):
    root = request.scope.get("root_path", "")
    if session:
        ledger.delete_session(session)
    resp = RedirectResponse(root + "/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------------------
# Bootstrap: /setup
# ---------------------------------------------------------------------------


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if ledger.any_admin_exists():
        return JSONResponse({"error": "not_found"}, status_code=404)
    return SETUP_HTML


@app.post("/setup")
async def setup_submit(request: Request):
    root = request.scope.get("root_path", "")
    if ledger.any_admin_exists():
        return JSONResponse({"error": "admin_already_exists"}, status_code=409)
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = form.get("password", "")
    confirm = form.get("confirm", "")
    if password != confirm:
        return RedirectResponse(
            root + "/setup?error=Passwords+do+not+match", status_code=303
        )
    errors = auth.validate_password(password)
    if errors:
        msg = "Password " + "; ".join(errors)
        return RedirectResponse(
            root + "/setup?error=" + msg.replace(" ", "+"), status_code=303
        )
    if not email:
        return RedirectResponse(
            root + "/setup?error=Email+is+required", status_code=303
        )
    user = ledger.create_user(
        username="admin",
        email=email,
        password_hash=auth.hash_password(password),
        roles=["admin"],
        must_change_password=False,  # admin chose their own password
    )

    # Fresh deployments have no signing keys — generate one now so the ledger
    # can sign entries without a separate `kyde keygen` step. Skip if a
    # key is already present (re-setup after DB wipe leaves keys intact).
    if _features.HAS_SIGNING:
        try:
            from .signing import PRIVATE_KEY_PATH, TPM_KEY_PATH, generate_keypair

            if not PRIVATE_KEY_PATH.exists() and not TPM_KEY_PATH.exists():
                generate_keypair()
        except Exception as exc:
            # Non-fatal: admin is already created. The operator can run
            # `kyde keygen` manually. Log and continue.
            print(f"[setup] warning: failed to auto-generate signing key: {exc}")

    token = _mint_session(user)
    resp = RedirectResponse(root + "/", status_code=303)
    _set_session_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# Change password (forced first-login + self-service)
# ---------------------------------------------------------------------------


@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(session: str | None = Cookie(None)):
    ctx = _session_ctx(session)
    if not ctx:
        # Middleware should already redirect to /login; belt-and-braces.
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _render_change_password_page(force=bool(ctx.get("must_change_password")))


@app.post("/api/change-password")
async def api_change_password(request: Request, session: str | None = Cookie(None)):
    ctx = _session_ctx(session)
    if not ctx:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    new_password = (body or {}).get("new_password") or ""
    current_password = (body or {}).get("current_password") or ""

    errors = auth.validate_password(new_password)
    if errors:
        return JSONResponse({"error": "Password " + "; ".join(errors)}, status_code=400)

    user_id = ctx["user_id"]
    # current-password check is skipped only on the forced-first-login path
    if not ctx.get("must_change_password"):
        stored = ledger.get_password_hash(user_id)
        if not stored or not auth.verify_password(current_password, stored):
            return JSONResponse(
                {"error": "Current password is incorrect"}, status_code=400
            )

    ledger.set_password(user_id, auth.hash_password(new_password))
    _refresh_session(session)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Self-service profile (/profile + /api/profile/*)
# ---------------------------------------------------------------------------


@app.post("/api/profile/email")
async def api_profile_email(request: Request, session: str | None = Cookie(None)):
    ctx = _session_ctx(session)
    if not ctx:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    email = (body or {}).get("email", "").strip()
    if not email or "@" not in email:
        return JSONResponse({"error": "Invalid email"}, status_code=400)
    ledger.update_user(ctx["user_id"], email=email)
    return Response(status_code=204)


@app.post("/api/profile/password")
async def api_profile_password(request: Request, session: str | None = Cookie(None)):
    """Self-service password change. Current password is always required here."""
    ctx = _session_ctx(session)
    if not ctx:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    current = (body or {}).get("current_password") or ""
    new_password = (body or {}).get("new_password") or ""
    errors = auth.validate_password(new_password)
    if errors:
        return JSONResponse({"error": "Password " + "; ".join(errors)}, status_code=400)
    stored = ledger.get_password_hash(ctx["user_id"])
    if not stored or not auth.verify_password(current, stored):
        return JSONResponse({"error": "Current password is incorrect"}, status_code=400)
    ledger.set_password(ctx["user_id"], auth.hash_password(new_password))
    _refresh_session(session)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Admin user management (/api/users/*)
# ---------------------------------------------------------------------------


def _admin_required(session: str | None):
    # Re-read roles from the DB on every admin-gated request so role
    # changes (UI-driven or direct SQL) take effect without a re-login.
    # Admin endpoints are low-frequency so the extra read is invisible.
    _refresh_session(session)
    ctx = _session_ctx(session)
    if not ctx:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    if "admin" not in ctx["roles"]:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return ctx, None


def _admin_or_auditor_required(session: str | None):
    """Same tuple shape as `_admin_required`, accepts either role.

    Used by DLP policy management: auditors triaging a FP flood need the
    same mute power admins have, since they're the role drowning in noise.
    """
    _refresh_session(session)
    ctx = _session_ctx(session)
    if not ctx:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    roles = ctx["roles"]
    if "admin" not in roles and "auditor" not in roles:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return ctx, None


@app.get("/api/hosts/resolve")
async def api_hosts_resolve(
    identifier: str = Query(..., min_length=1),
    session: str | None = Cookie(None),
):
    """Bidirectional host lookup.

    - If `identifier` looks like an IP, return the cached/resolved hostname
      (triggers the lazy resolver if absent/stale).
    - Otherwise treat it as a hostname and return the matching IPs, sorted
      most-recently-seen first. Most callers route to the first IP; the
      `ips` array is exposed so the host page can render a picker when
      there's a tie.
    """
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import host_resolver

    # IP detection — loose, by intent. malformed strings hit the hostname
    # branch and resolve to empty, which is the right empty-state.
    import re

    is_ip = (
        bool(re.match(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$", identifier))
        or ":" in identifier
    )

    if is_ip:
        try:
            resolution = await host_resolver.resolve_and_cache(identifier)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "kind": "ip",
            "ip": identifier,
            "hostname": resolution.hostname,
            "hostname_source": resolution.source,
            "ips": [identifier],
        }

    matches = ledger.find_ips_for_hostname(identifier)
    return {
        "kind": "hostname",
        "hostname": identifier,
        "ips": [
            {
                "ip": m["ip"],
                "source": m["source"],
                "last_seen": (
                    float(m["last_seen"]) if m["last_seen"] is not None else None
                ),
            }
            for m in matches
        ],
    }


@app.get("/api/host-labels")
def api_host_labels_list(
    status: str = Query("all", pattern="^(all|labeled|unlabeled|recently_active)$"),
    q: str | None = Query(None),
    session: str | None = Cookie(None),
):
    """Rows for the Settings Host Names table. `status` mirrors the chip
    state, `q` is a substring search across IP + hostname. Limited to 100
    rows per call — the UI surfaces a banner if more exist."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    recently_since: float | None = None
    if status == "recently_active":
        import time as _time

        recently_since = _time.time() - 86400  # last 24h

    try:
        rows = ledger.list_host_resolutions(
            status=status,
            search=q or None,
            recently_active_since=recently_since,
            limit=100,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    out = []
    for r in rows:
        source = r.get("source")
        out.append(
            {
                "ip": r["ip"],
                "hostname": r.get("hostname"),
                # "dns miss" surfaced as a distinct UI source so the
                # admin can tell "we tried and failed" apart from "never
                # asked".
                "source": (
                    "admin"
                    if source == "admin"
                    else (
                        "dns miss"
                        if source == "dns" and not r.get("hostname")
                        else ("dns" if source == "dns" else None)
                    )
                ),
                "resolved_at": (
                    r["resolved_at"].isoformat()
                    if r.get("resolved_at") is not None
                    else None
                ),
                "last_seen": (
                    float(r["last_seen"]) if r.get("last_seen") is not None else None
                ),
                "last_seen_iso": (
                    datetime.fromtimestamp(float(r["last_seen"])).isoformat()
                    if r.get("last_seen") is not None
                    else None
                ),
            }
        )
    return out


@app.put("/api/host-labels/{ip:path}")
async def api_host_labels_put(
    ip: str,
    request: Request,
    session: str | None = Cookie(None),
):
    """Admin-only: set or update an admin label for `ip`. Body
    `{hostname: string}`. Empty hostname rejected; use DELETE to clear."""
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    hostname = body.get("hostname") if isinstance(body, dict) else None
    if not isinstance(hostname, str) or not hostname.strip():
        return JSONResponse(
            {"error": "hostname is required; use DELETE to clear"},
            status_code=400,
        )
    try:
        row = ledger.upsert_host_label(
            ip=ip, hostname=hostname, by_user_id=ctx["user_id"]
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {
        "ip": row["ip"],
        "hostname": row["hostname"],
        "source": row["source"],
    }


@app.delete("/api/host-labels/{ip:path}")
def api_host_labels_delete(ip: str, session: str | None = Cookie(None)):
    """Admin-only: remove an admin label for `ip`. DNS may repopulate on
    the next read of the host page."""
    ctx, err = _admin_required(session)
    if err:
        return err
    if not ledger.delete_host_label(ip):
        return JSONResponse({"error": "no admin label found"}, status_code=404)
    return {"ip": ip, "cleared": True}


@app.post("/api/host-labels/{ip:path}/refresh")
async def api_host_labels_refresh(ip: str, session: str | None = Cookie(None)):
    """Admin-only: force a reverse-DNS refresh for `ip`, bypassing TTL.
    Respects admin precedence — never overwrites an admin label."""
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import host_resolver

    resolution = await host_resolver.resolve_and_cache(ip, force=True)
    return {
        "ip": resolution.ip,
        "hostname": resolution.hostname,
        "source": resolution.source,
    }


@app.get("/api/agents")
def api_agents_list(session: str | None = Cookie(None)):
    """List every known agent with display_name + activity rollups.

    Open to any authenticated user — agent labels are not sensitive and
    show up across the dashboard. Admin-only mutation is on the PATCH.
    """
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = ledger.list_agents()
    for r in rows:
        r["first_seen_dt"] = datetime.fromtimestamp(r["first_seen"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        r["last_seen_dt"] = datetime.fromtimestamp(r["last_seen"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    return rows


@app.get("/api/agent-blocks")
def api_agent_blocks_list(session: str | None = Cookie(None)):
    """Every blocked agent. Open to authenticated users (read-only)."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _features.HAS_ENFORCEMENT:
        # Sandbox edition: no enforcement, so there is no block list.
        return []
    rows = _features.enforce.list_agent_blocks()
    return [
        {
            "agent_id": r["agent_id"],
            "blocked_at": float(r["blocked_at"]),
            "blocked_by": int(r["blocked_by"]) if r["blocked_by"] is not None else None,
            "reason": r["reason"],
        }
        for r in rows
    ]


@app.post("/api/agents/{agent_id}/block")
async def api_agent_block(
    agent_id: str,
    request: Request,
    session: str | None = Cookie(None),
):
    """Admin-only: add `agent_id` to the proxy block list. Body: `{reason?}`.
    Returns 404 if the agent has never been observed (no agents row)."""
    ctx, err = _admin_required(session)
    if err:
        return err
    if not _features.HAS_ENFORCEMENT:
        return JSONResponse(
            {
                "error": (
                    "Agent blocking is an enterprise enforcement feature, "
                    "not enabled in this edition."
                )
            },
            status_code=404,
        )
    try:
        body = await request.json() if request.headers.get("content-type") else {}
    except Exception:
        body = {}
    reason = str(body.get("reason", "")) if isinstance(body, dict) else ""
    try:
        row = _features.enforce.block_agent(
            agent_id, blocked_by=ctx["user_id"], reason=reason
        )
    except Exception as exc:
        # Foreign key violation on agents.agent_id surfaces here.
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {
        "agent_id": row["agent_id"],
        "blocked_at": float(row["blocked_at"]),
        "reason": row["reason"],
    }


@app.delete("/api/agents/{agent_id}/block")
def api_agent_unblock(agent_id: str, session: str | None = Cookie(None)):
    """Admin-only: remove `agent_id` from the block list."""
    ctx, err = _admin_required(session)
    if err:
        return err
    if not _features.HAS_ENFORCEMENT:
        return JSONResponse(
            {
                "error": (
                    "Agent blocking is an enterprise enforcement feature, "
                    "not enabled in this edition."
                )
            },
            status_code=404,
        )
    if not _features.enforce.unblock_agent(agent_id):
        return JSONResponse({"error": "not blocked"}, status_code=404)
    return {"agent_id": agent_id, "blocked": False}


@app.patch("/api/agents/{agent_id}")
async def api_agents_update(
    agent_id: str,
    request: Request,
    session: str | None = Cookie(None),
):
    """Admin-only: set or clear an agent's display_name.

    Body: `{"display_name": "CRM Coding Agent"}` (or null to clear).
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict) or "display_name" not in body:
        return JSONResponse(
            {"error": "body must include display_name"}, status_code=400
        )
    new_name = body["display_name"]
    if new_name is not None and not isinstance(new_name, str):
        return JSONResponse(
            {"error": "display_name must be string or null"}, status_code=400
        )
    updated = ledger.set_agent_display_name(agent_id, new_name)
    if not updated:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    return {"agent_id": agent_id, "display_name": new_name}


# ---------------------------------------------------------------------------
# Per-agent traffic inventory + mode flip
# (see migration 0011_agent_traffic.sql + project memory
#  project_per_agent_traffic_metering.md)
# ---------------------------------------------------------------------------


@app.get("/api/agent-traffic")
def api_agent_traffic(
    agent_id: str | None = Query(None),
    session: str | None = Cookie(None),
):
    """Return every (agent_id, path_kind) meter joined with its current mode.

    Optional `agent_id` query param scopes to one agent — used by the
    agent-detail page's Traffic Inventory section. Open to any authenticated
    user (auditors and admins both need to see what's flowing); mode flips
    are admin-only via the POST endpoint below.
    """
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = ledger.list_agent_traffic(agent_id=agent_id)
    return {
        "items": [
            {
                "agent_id": r["agent_id"],
                "path_kind": r["path_kind"],
                "count": int(r["count"]),
                "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "mode": r["mode"],
            }
            for r in rows
        ]
    }


@app.post("/api/agent-traffic/{agent_id}/{path_kind}/mode")
async def api_agent_traffic_set_mode(
    agent_id: str,
    path_kind: str,
    request: Request,
    session: str | None = Cookie(None),
):
    """Admin-only: flip (agent_id, path_kind) between count_only and full_logging.

    Body: `{"mode": "count_only" | "full_logging"}`. Appends a row to
    agent_traffic_mode_history; the latest row wins for proxy mode lookups.
    Phase B1: the proxy doesn't yet act on this mode (still count-only
    behavior for non-chat paths). Phase B2 wires the actual full-logging.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict) or "mode" not in body:
        return JSONResponse({"error": "body must include mode"}, status_code=400)
    try:
        row = ledger.set_agent_traffic_mode(
            agent_id=agent_id,
            path_kind=path_kind,
            mode=body["mode"],
            changed_by=ctx["user_id"],
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {
        "agent_id": row["agent_id"],
        "path_kind": row["path_kind"],
        "mode": row["mode"],
        "changed_at": row["changed_at"].isoformat() if row["changed_at"] else None,
    }


@app.get("/api/users")
def api_users_list(
    include_deleted: int = Query(0),
    session: str | None = Cookie(None),
):
    ctx, err = _admin_required(session)
    if err:
        return err
    users = ledger.list_users(include_deleted=bool(include_deleted))
    return users


@app.post("/api/users")
async def api_users_create(request: Request, session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip()
    roles = body.get("roles") or []

    if not username:
        return JSONResponse({"error": "Username is required"}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "Valid email is required"}, status_code=400)
    if not isinstance(roles, list) or not roles:
        return JSONResponse({"error": "At least one role is required"}, status_code=400)
    bad_roles = [r for r in roles if r not in ledger.VALID_ROLES]
    if bad_roles:
        return JSONResponse({"error": f"Unknown roles: {bad_roles}"}, status_code=400)

    # Self-elevation guard doesn't apply to create (caller can't be the new user),
    # but admins still shouldn't be able to spawn a fresh admin+auditor account to
    # bypass the rule. We enforce this only on PATCH (self-update) per the plan.

    if ledger.get_user_by_username(username, include_deleted=True):
        return JSONResponse({"error": "Username already exists"}, status_code=409)

    temp_pw = auth.generate_temp_password()
    user = ledger.create_user(
        username=username,
        email=email,
        password_hash=auth.hash_password(temp_pw),
        roles=roles,
        must_change_password=True,
    )
    return {"user": user, "temp_password": temp_pw}


@app.patch("/api/users/{user_id}")
async def api_users_update(
    user_id: int,
    request: Request,
    session: str | None = Cookie(None),
):
    ctx, err = _admin_required(session)
    if err:
        return err
    target = ledger.get_user_by_id(user_id, include_deleted=True)
    if not target or target["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    updates: dict = {}
    if "email" in body:
        email = (body["email"] or "").strip()
        if not email or "@" not in email:
            return JSONResponse({"error": "Invalid email"}, status_code=400)
        updates["email"] = email
    if "enabled" in body:
        updates["enabled"] = bool(body["enabled"])
    if "roles" in body:
        roles = body["roles"] or []
        if not isinstance(roles, list) or not roles:
            return JSONResponse(
                {"error": "At least one role is required"}, status_code=400
            )
        bad = [r for r in roles if r not in ledger.VALID_ROLES]
        if bad:
            return JSONResponse({"error": f"Unknown roles: {bad}"}, status_code=400)
        # Self-elevation guard: an admin cannot grant themselves auditor.
        if (
            user_id == ctx["user_id"]
            and "auditor" in roles
            and "auditor" not in ctx["roles"]
        ):
            return JSONResponse({"error": "self_elevation_forbidden"}, status_code=403)
        # Last-admin guard on role removal.
        if "admin" not in roles and "admin" in target["roles"]:
            if ledger.count_active_admins(exclude_user_id=user_id) == 0:
                return JSONResponse({"error": "last_admin"}, status_code=409)
        updates["roles"] = roles

    # Disable guard: don't let the caller disable the last admin (themselves or other).
    if updates.get("enabled") is False and "admin" in target["roles"]:
        if ledger.count_active_admins(exclude_user_id=user_id) == 0:
            return JSONResponse({"error": "last_admin"}, status_code=409)

    updated = ledger.update_user(user_id, **updates)
    # If we changed the caller's own roles/enabled, refresh the cached session
    # so the sidebar updates on next page load without re-login.
    if user_id == ctx["user_id"]:
        _refresh_session(session)
    # On role change, drop other sessions for this user so new capabilities
    # apply immediately (the current session we just refreshed).
    if "roles" in updates:
        ledger.delete_sessions_for_user(user_id, except_token=session)
    return updated


@app.post("/api/users/{user_id}/reset-password")
def api_users_reset_password(
    user_id: int,
    session: str | None = Cookie(None),
):
    ctx, err = _admin_required(session)
    if err:
        return err
    target = ledger.get_user_by_id(user_id, include_deleted=True)
    if not target or target["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    temp_pw = auth.generate_temp_password()
    ledger.set_temp_password(user_id, auth.hash_password(temp_pw))
    # Force re-login on all of this user's existing sessions.
    _invalidate_user_sessions(user_id)
    return {"temp_password": temp_pw}


@app.post("/api/users/{user_id}/unlock")
def api_users_unlock(user_id: int, session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    target = ledger.get_user_by_id(user_id, include_deleted=True)
    if not target or target["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    ledger.unlock_user(user_id)
    return Response(status_code=204)


@app.delete("/api/users/{user_id}")
def api_users_delete(user_id: int, session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    if user_id == ctx["user_id"]:
        return JSONResponse({"error": "cannot_delete_self"}, status_code=400)
    target = ledger.get_user_by_id(user_id, include_deleted=True)
    if not target or target["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if "admin" in target["roles"]:
        if ledger.count_active_admins(exclude_user_id=user_id) == 0:
            return JSONResponse({"error": "last_admin"}, status_code=409)
    ledger.soft_delete_user(user_id)
    _invalidate_user_sessions(user_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# MCP server registry (/api/mcp/servers*) — admin-only routing-table CRUD.
#
# The registry is a pure routing table: no credentials, no upstream auth
# storage. The agent sends its own Authorization header on each call to
# /mcp/{name}; we forward it unchanged — credential handling is deliberately
# out of scope (see src/kyde/mcp_proxy.py).
#
# Path identifier is {name} — the same handle that appears in the routing
# URL — rather than {id}. Names are unique per tenant and stable; renames
# aren't a v1 affordance (delete + recreate).
#
# `probe_tools` lets the dashboard fetch an upstream's tools/list using a
# one-off bearer the operator pastes into the UI. The token is forwarded
# once and never persisted. Rationale: the per-server detail page needs
# tool names to build the policy matrix (M3) without waiting for live
# agent traffic to populate it.
# ---------------------------------------------------------------------------


def _mcp_server_payload(row: dict) -> dict:
    """API shape — drops the tenant column (single-tenant today) and
    normalises timestamps to ISO so the frontend doesn't deal with two
    serializations. last_* fields land on rows automatically once
    migration 0016 is applied and the first call lands."""
    created_at = row.get("created_at")
    last_call_at = row.get("last_call_at")
    last_error_at = row.get("last_error_at")
    return {
        "id": row["id"],
        "name": row["name"],
        "upstream_url": row["upstream_url"],
        "enabled": bool(row["enabled"]),
        "created_at": created_at.isoformat() if created_at else None,
        "created_by": row.get("created_by"),
        "last_call_at": last_call_at.isoformat() if last_call_at else None,
        "last_error_at": last_error_at.isoformat() if last_error_at else None,
        "last_error_status": row.get("last_error_status"),
        "last_error_snippet": row.get("last_error_snippet"),
    }


@app.get("/api/mcp/servers")
def api_mcp_servers_list(session: str | None = Cookie(None)):
    """List MCP servers. Viewer-or-above; the registry is read-only here."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import mcp_registry

    return {"items": [_mcp_server_payload(r) for r in mcp_registry.list_servers()]}


@app.post("/api/mcp/servers")
async def api_mcp_servers_create(request: Request, session: str | None = Cookie(None)):
    """Admin-only: register a new MCP server. Body: {name, upstream_url, enabled?}."""
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_registry

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)

    name = (body.get("name") or "").strip()
    upstream_url = (body.get("upstream_url") or "").strip()
    enabled = bool(body.get("enabled", True))
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not upstream_url:
        return JSONResponse({"error": "upstream_url is required"}, status_code=400)

    # Conflict surface — 409 reads better than the silent-overwrite the
    # underlying upsert would do.
    if mcp_registry.get_server(name) is not None:
        return JSONResponse(
            {"error": f"server {name!r} already exists"}, status_code=409
        )

    try:
        row = mcp_registry.upsert_server(
            name, upstream_url, enabled=enabled, user_id=ctx["user_id"]
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    payload = _mcp_server_payload(row)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="mcp_server.create",
        resource_type="mcp_server",
        resource_id=name,
        before=None,
        after=payload,
    )
    return payload


@app.patch("/api/mcp/servers/{name}")
async def api_mcp_servers_update(
    name: str, request: Request, session: str | None = Cookie(None)
):
    """Admin-only: change upstream_url and/or enabled. Renames not supported in v1."""
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_registry

    existing = mcp_registry.get_server(name)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    before_payload = _mcp_server_payload(existing)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)

    upstream_url = body.get("upstream_url", existing["upstream_url"])
    if not isinstance(upstream_url, str) or not upstream_url.strip():
        return JSONResponse(
            {"error": "upstream_url must be a non-empty string"}, status_code=400
        )
    enabled = body.get("enabled", existing["enabled"])
    if not isinstance(enabled, bool):
        return JSONResponse({"error": "enabled must be boolean"}, status_code=400)

    try:
        row = mcp_registry.upsert_server(
            name, upstream_url.strip(), enabled=enabled, user_id=ctx["user_id"]
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    payload = _mcp_server_payload(row)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="mcp_server.update",
        resource_type="mcp_server",
        resource_id=name,
        before=before_payload,
        after=payload,
    )
    return payload


@app.delete("/api/mcp/servers/{name}")
def api_mcp_servers_delete(name: str, session: str | None = Cookie(None)):
    """Admin-only: remove the server (cascades to mcp_tool_policies)."""
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_registry

    existing = mcp_registry.get_server(name)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if not mcp_registry.delete_server(name):
        return JSONResponse({"error": "not_found"}, status_code=404)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="mcp_server.delete",
        resource_type="mcp_server",
        resource_id=name,
        before=_mcp_server_payload(existing),
        after=None,
    )
    return Response(status_code=204)


@app.post("/api/mcp/servers/{name}/probe-tools")
async def api_mcp_servers_probe_tools(
    name: str, request: Request, session: str | None = Cookie(None)
):
    """Admin-only: fetch the upstream's tools/list using an operator-supplied
    one-off bearer token. Token is forwarded once and never persisted —
    used to seed the per-server detail page before live traffic exists.

    Body: {"authorization": "Bearer <token>"} (the full header value).
    Returns the upstream tools/list response as-is, or an error envelope.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_registry, mcp_proxy

    backend = mcp_registry.get_server(name)
    if backend is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    auth_header = body.get("authorization")
    if not isinstance(auth_header, str) or not auth_header.strip():
        return JSONResponse({"error": "authorization is required"}, status_code=400)

    envelope = {"jsonrpc": "2.0", "id": "probe", "method": "tools/list"}
    raw = json.dumps(envelope).encode()
    forward_headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    try:
        async with httpx.AsyncClient(timeout=mcp_proxy._UPSTREAM_TIMEOUT_S) as client:
            upstream = await client.post(
                backend["upstream_url"],
                headers=forward_headers,
                content=raw,
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"upstream transport error: {exc.__class__.__name__}"},
            status_code=502,
        )

    # Parse the JSON-RPC envelope so we return a structured payload to the
    # UI rather than opaque bytes. We don't enforce SSE here — probe is a
    # one-shot call against a server that the operator picked for the UI.
    try:
        parsed = upstream.json()
    except Exception:
        return JSONResponse(
            {
                "error": "upstream returned non-JSON response",
                "status_code": upstream.status_code,
            },
            status_code=502,
        )

    # M4: seed the aggregator catalog from the probe — gives operators a
    # way to populate /mcp/ without waiting for live agent traffic.
    if 200 <= upstream.status_code < 300 and isinstance(parsed, dict):
        result = parsed.get("result")
        if isinstance(result, dict):
            tools = result.get("tools")
            if isinstance(tools, list):
                from . import mcp_aggregator

                mcp_aggregator.seed_from_tools_list(name, tools)

    return JSONResponse(parsed, status_code=upstream.status_code)


# ---------------------------------------------------------------------------
# Aggregator catalog (/api/mcp/aggregator/catalog) — viewer-or-above.
#
# Pure read surface for the dashboard banner: shows the current
# namespaced tool list, how many servers contribute, and how old the
# oldest entry is so operators know when to re-probe. The catalog itself
# is seeded opportunistically from real `tools/list` traffic and from
# probe-tools runs — no server-side fanout from this endpoint.
# ---------------------------------------------------------------------------


@app.get("/api/mcp/aggregator/catalog")
def api_mcp_aggregator_catalog(session: str | None = Cookie(None)):
    """Read-only catalog snapshot. Open to any authenticated user."""
    if not _session_ctx(session):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import mcp_aggregator

    return mcp_aggregator.catalog_snapshot()


# ---------------------------------------------------------------------------
# Per-tool policies (/api/mcp/servers/{name}/policies*) — admin-only.
#
# A policy row is (server, agent_id, tool_name) → {decision, reason}, where
# `*` is a literal wildcard in agent_id and tool_name. The proxy hot path
# applies most-specific-wins precedence (mcp_policy.check_policy); these
# endpoints are just CRUD surfaces for the dashboard matrix.
# ---------------------------------------------------------------------------


def _mcp_policy_payload(row: dict) -> dict:
    """API shape — normalises timestamps and drops the updated_by user_id
    in favour of just letting the frontend join on it later if it needs to."""
    updated_at = row.get("updated_at")
    return {
        "server_id": str(row["server_id"]),
        "agent_id": row["agent_id"],
        "tool_name": row["tool_name"],
        "decision": row["decision"],
        "reason": row.get("reason"),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "updated_by": row.get("updated_by"),
    }


@app.get("/api/mcp/servers/{name}/policies")
def api_mcp_policies_list(name: str, session: str | None = Cookie(None)):
    """Admin-only: list all (server, agent, tool) policy rows.

    Empty list ⇒ default-allow everywhere on this server.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_policy, mcp_registry

    backend = mcp_registry.get_server(name)
    if backend is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    rows = mcp_policy.list_policies(str(backend["id"]))
    return {"items": [_mcp_policy_payload(r) for r in rows]}


@app.put("/api/mcp/servers/{name}/policies/{agent_id}/{tool_name}")
async def api_mcp_policies_set(
    name: str,
    agent_id: str,
    tool_name: str,
    request: Request,
    session: str | None = Cookie(None),
):
    """Admin-only: insert-or-update a policy row.

    Body: {"decision": "allow"|"deny", "reason"?: string}. agent_id and
    tool_name accept the literal `*` wildcard; precedence is enforced at
    proxy time.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_policy, mcp_registry

    backend = mcp_registry.get_server(name)
    if backend is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    decision = body.get("decision")
    if decision not in ("allow", "deny"):
        return JSONResponse(
            {"error": "decision must be 'allow' or 'deny'"}, status_code=400
        )
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        return JSONResponse({"error": "reason must be a string"}, status_code=400)
    if not agent_id or not tool_name:
        return JSONResponse(
            {"error": "agent_id and tool_name are required"}, status_code=400
        )

    existing = next(
        (
            r
            for r in mcp_policy.list_policies(str(backend["id"]))
            if r["agent_id"] == agent_id and r["tool_name"] == tool_name
        ),
        None,
    )
    try:
        row = mcp_policy.upsert_policy(
            str(backend["id"]),
            agent_id,
            tool_name,
            decision,
            (reason or None),
            ctx["user_id"],
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    payload = _mcp_policy_payload(row)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="mcp_policy.set",
        resource_type="mcp_policy",
        resource_id=f"{name}/{agent_id}/{tool_name}",
        before=_mcp_policy_payload(existing) if existing else None,
        after=payload,
    )
    return payload


@app.delete("/api/mcp/servers/{name}/policies/{agent_id}/{tool_name}")
def api_mcp_policies_delete(
    name: str,
    agent_id: str,
    tool_name: str,
    session: str | None = Cookie(None),
):
    """Admin-only: remove a policy row (the (agent, tool) reverts to whatever
    less-specific row matches, or default-allow if nothing else does)."""
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import mcp_policy, mcp_registry

    backend = mcp_registry.get_server(name)
    if backend is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    existing = next(
        (
            r
            for r in mcp_policy.list_policies(str(backend["id"]))
            if r["agent_id"] == agent_id and r["tool_name"] == tool_name
        ),
        None,
    )
    if not mcp_policy.delete_policy(str(backend["id"]), agent_id, tool_name):
        return JSONResponse({"error": "not_found"}, status_code=404)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="mcp_policy.delete",
        resource_type="mcp_policy",
        resource_id=f"{name}/{agent_id}/{tool_name}",
        before=_mcp_policy_payload(existing) if existing else None,
        after=None,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Admin action audit log (/api/audit-log) — admin-only.
#
# Operational telemetry, not part of the signed chain of custody. Filters
# (actor, action, resource_type) are AND-combined. Newest first.
# ---------------------------------------------------------------------------


@app.get("/api/audit-log")
def api_audit_log(
    session: str | None = Cookie(None),
    limit: int = 100,
    offset: int = 0,
    actor_id: int | None = None,
    action: str | None = None,
    resource_type: str | None = None,
):
    ctx, err = _admin_required(session)
    if err:
        return err
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    return audit_log.list_actions(
        limit=limit,
        offset=offset,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
    )


# ---------------------------------------------------------------------------
# DLP regex policies (/api/dlp-policies*).
#
# READ is admin OR auditor — auditors get read-only visibility into the
# detection posture. WRITES (toggle / bulk / resync) are admin-only: changing
# whether an entire pattern fires is a configuration change, not triage.
# Auditors still disposition individual alerts via the alert-transition
# endpoint; they just can't disable a whole pattern.
#
# Read model: a row per bundled pattern (the YAML files baked into the
# gateway image), enriched with hit counts pulled from dlp_alerts.
# Disabled patterns still appear; the operator can re-enable them.
#
# Writes are gateway-pushed: every toggle rewrites the active set in
# dlp-regex via POST /v1/patterns/replace. Manual rule creation is out
# of scope for v1 — the YAML in dlp-patterns/ is the curation surface.
# ---------------------------------------------------------------------------


@app.get("/api/dlp-policies")
def api_dlp_policies_list(session: str | None = Cookie(None)):
    """Admin or auditor: list bundled patterns with enabled + hit count."""
    ctx, err = _admin_or_auditor_required(session)
    if err:
        return err
    from . import dlp_policies

    return {"items": dlp_policies.list_for_ui()}


@app.patch("/api/dlp-policies/{pattern_id}")
async def api_dlp_policies_update(
    pattern_id: str, request: Request, session: str | None = Cookie(None)
):
    """Admin only: toggle a pattern.
    Body: {"enabled"?: bool, "prevention"?: bool} — at least one field.

    "enabled" re-pushes the resulting active set to dlp-regex before
    returning so the caller can trust the new state is already live.
    "prevention" is a gateway-side decision flag (block vs detect-only
    when the global Policy Prevention setting is on) — no push needed.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import dlp_policies

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    has_enabled = isinstance(body, dict) and isinstance(body.get("enabled"), bool)
    has_prevention = isinstance(body, dict) and isinstance(body.get("prevention"), bool)
    if not (has_enabled or has_prevention):
        return JSONResponse(
            {
                "error": 'body must include {"enabled": bool} and/or {"prevention": bool}'
            },
            status_code=400,
        )
    # Prevention is enforcement. Sandbox images ship without the `enforce`
    # package, so the prevention flag must not be settable — detection
    # (`enabled`) stays available. Defence-in-depth behind the locked UI.
    if has_prevention and not _features.HAS_ENFORCEMENT:
        return JSONResponse(
            {
                "error": "Inline prevention is part of enforcement — not available in this edition."
            },
            status_code=404,
        )
    row = None
    try:
        if has_prevention:
            row = dlp_policies.set_prevention(
                pattern_id, body["prevention"], ctx.get("user_id")
            )
        if has_enabled:
            row = await dlp_policies.set_enabled(
                pattern_id, body["enabled"], ctx.get("user_id")
            )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except httpx.HTTPError as exc:
        # The DB write already committed; surfacing the push failure as
        # 502 tells the caller "your toggle stuck but dlp-regex didn't
        # accept it" — they can retry with /resync.
        return JSONResponse(
            {"error": f"toggle persisted but dlp-regex push failed: {exc}"},
            status_code=502,
        )
    after: dict = {}
    if has_enabled:
        after["enabled"] = bool(body["enabled"])
    if has_prevention:
        after["prevention"] = bool(body["prevention"])
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action=(
            "dlp_policy.prevention_toggle"
            if has_prevention and not has_enabled
            else "dlp_policy.toggle"
        ),
        resource_type="dlp_policy",
        resource_id=pattern_id,
        before=None,
        after=after,
    )
    return row


@app.post("/api/dlp-policies/prevention-bulk")
async def api_dlp_policies_prevention_bulk(
    request: Request, session: str | None = Cookie(None)
):
    """Admin only: flip prevention on/off for EVERY bundled pattern.
    Body: {"enabled": bool}. Backs the enable-all / disable-all buttons
    on the Policies page. Gateway-side only — no dlp-regex push."""
    ctx, err = _admin_required(session)
    if err:
        return err
    if not _features.HAS_ENFORCEMENT:
        return JSONResponse(
            {
                "error": "Inline prevention is part of enforcement — not available in this edition."
            },
            status_code=404,
        )
    from . import dlp_policies

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        return JSONResponse(
            {"error": 'body must be {"enabled": bool}'}, status_code=400
        )
    result = dlp_policies.set_prevention_bulk(body["enabled"], ctx.get("user_id"))
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="dlp_policy.prevention_bulk",
        resource_type="dlp_policy",
        resource_id=None,
        before=None,
        after={"enabled": bool(body["enabled"]), **result},
    )
    return result


@app.post("/api/dlp-policies/resync")
async def api_dlp_policies_resync(session: str | None = Cookie(None)):
    """Admin only: force a re-push of the active set to dlp-regex.

    Belt-and-braces escape hatch wired to the page-header button on the
    Policies page. The scan-path observer normally catches dlp-regex
    restarts, but this gives the operator a manual trigger when they
    want to know the push succeeded right now.
    """
    ctx, err = _admin_required(session)
    if err:
        return err
    from . import dlp_policies

    try:
        body = await dlp_policies.push_active_set()
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"dlp-regex push failed: {exc}"}, status_code=502)
    audit_log.record(
        actor_id=ctx.get("user_id"),
        actor_username=ctx.get("username"),
        action="dlp_policy.resync",
        resource_type="dlp_policy",
        resource_id=None,
        before=None,
        after=None,
    )
    return body


# ---------------------------------------------------------------------------
# Runtime-tunable settings (/api/settings*)
# ---------------------------------------------------------------------------
#
# This dashboard container is a pure JSON/auth backend. It handles the
# server-rendered auth flows (/login, /setup, /change-password) and every
# /api/* route, but the SPA shell and /assets/* are served by `kyde-ui`
# which reverse-proxies /api/ + /login + /setup + /openapi.json back here.
#
# `GET /` only fires when someone bypasses nginx (e.g. curling the
# container directly); return a short note so that case is obvious.


def _audit_setting_change(
    ctx: dict, key: str, old: object, new: object, action: str
) -> None:
    """Write an auditable ledger entry recording a settings change. Mirrors
    the proxy's LLM entries: same signed, hash-chained format so the change
    is provable after the fact."""
    try:
        ledger.append(
            agent_id=f"admin:{ctx.get('username') or ctx.get('user_id')}",
            action_type="setting_change",
            model="",
            request_body={"key": key, "action": action, "old": old, "new": new},
            response_body={},
            why_messages=[],
            tool_calls=[],
            client_ip="",
            user_agent="",
            session_id="",
            upstream="",
            full_messages=[],
        )
    except Exception as exc:  # pragma: no cover
        print(f"  ⚠ settings audit: failed to write ledger entry: {exc}")


# Keys whose value is a secret. The raw ciphertext / password hash is
# never returned by the API; the item gets a `is_set` boolean instead so
# the UI can render "••••••••" vs empty.
_REDACTED_SETTING_KEYS = {"SMTP_PASSWORD_ENC"}

# Global prevention master switches — enforcement controls that the sandbox
# edition (no `enforce` package) must refuse to set. See api_settings_update.
_ENFORCEMENT_SETTING_KEYS = {"DLP_REGEX_PREVENTION", "DLP_BERT_PREVENTION"}


@app.get("/api/settings")
def api_settings_list(session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    items = settings_module.list_all()
    # Hydrate updated_by into a username for nicer display.
    user_ids = {item["updated_by"] for item in items if item["updated_by"] is not None}
    usernames: dict[int, str] = {}
    for uid in user_ids:
        u = ledger.get_user_by_id(int(uid), include_deleted=True)
        if u:
            usernames[int(uid)] = u["username"]
    for item in items:
        uid = item.get("updated_by")
        item["updated_by_username"] = (
            usernames.get(int(uid)) if uid is not None else None
        )
        if item["key"] in _REDACTED_SETTING_KEYS:
            item["is_set"] = bool(item.get("value"))
            item["value"] = ""
    return items


@app.patch("/api/settings/{key}")
async def api_settings_update(
    key: str,
    request: Request,
    session: str | None = Cookie(None),
):
    ctx, err = _admin_required(session)
    if err:
        return err
    if key not in settings_module.SPECS:
        return JSONResponse({"error": "unknown_key"}, status_code=404)
    # The global prevention master switches are enforcement controls. In the
    # sandbox edition (no `enforce` package) they must not be settable, even
    # via a direct API call past the locked UI. Defence-in-depth.
    if key in _ENFORCEMENT_SETTING_KEYS and not _features.HAS_ENFORCEMENT:
        return JSONResponse(
            {
                "error": "Inline prevention is part of enforcement — not available in this edition."
            },
            status_code=404,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    value = body.get("value")
    if value is None:
        return JSONResponse({"error": "value required"}, status_code=400)

    raw = str(value)

    # Encrypted-at-rest settings: the UI posts plaintext; we encrypt here
    # before handing it to the settings layer. Empty string = "no change",
    # so the UI can show "••••••••" and leave the input blank when nothing
    # is being edited.
    if key in _REDACTED_SETTING_KEYS:
        if raw == "":
            # Nothing to persist. Return the current state unchanged.
            _cur, _src = settings_module.get_with_source(key)
            return {"key": key, "value": "", "is_set": bool(_cur), "updated_at": None}
        try:
            raw = crypto.encrypt(raw)
        except Exception as exc:
            return JSONResponse({"error": f"failed to encrypt: {exc}"}, status_code=500)

    # Capture the pre-change value for the audit entry.
    old_value, _old_source = settings_module.get_with_source(key)

    try:
        row = settings_module.set_value(key, raw, ctx["user_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Never echo ciphertext or secrets back over the API.
    if key in _REDACTED_SETTING_KEYS:
        _audit_setting_change(ctx, key, "(redacted)", "(redacted)", "set")
        return {
            "key": key,
            "value": "",
            "is_set": True,
            "updated_at": row["updated_at"],
        }

    _audit_setting_change(ctx, key, old_value, row["value"], "set")
    return {"key": key, "value": row["value"], "updated_at": row["updated_at"]}


@app.post("/api/settings/smtp/test")
async def api_settings_smtp_test(session: str | None = Cookie(None)):
    """Send a canned test email using the currently-saved SMTP config to
    every auditor. Does NOT touch the dlp_alerts table. Returns the same
    error the worker would surface, so admins get immediate feedback."""
    ctx, err = _admin_required(session)
    if err:
        return err

    if not bool(settings_module.get("SMTP_ENABLED")):
        return JSONResponse(
            {"ok": False, "error": "SMTP_ENABLED is off"}, status_code=400
        )

    try:
        cfg = smtp_sender.load_smtp_config()
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    try:
        recipients = ledger.get_auditor_emails()
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"failed to load auditor list: {exc}"},
            status_code=500,
        )
    if not recipients:
        return JSONResponse(
            {
                "ok": False,
                "error": "no users with the 'auditor' role have an email set",
                "recipients": 0,
            },
            status_code=400,
        )

    try:
        await smtp_sender.send_test_email(cfg, recipients)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc), "recipients": len(recipients)},
            status_code=502,
        )

    return {"ok": True, "recipients": len(recipients)}


# ---------------------------------------------------------------------------
# DLP allow-list rules (v1: kind='allow' only; block is reserved).
# ---------------------------------------------------------------------------


@app.get("/api/dlp-rules")
def api_dlp_rules_list(session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    return ledger.list_dlp_rules()


@app.post("/api/dlp-rules")
async def api_dlp_rules_create(
    request: Request,
    session: str | None = Cookie(None),
):
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    kind = str(body.get("kind") or "allow")
    if kind != "allow":
        # Phase 1: only allow-rules are implemented. Blocking is a
        # separate design decision (see conversation with the user).
        return JSONResponse(
            {"error": "only kind='allow' is supported in this version"},
            status_code=400,
        )

    try:
        row = ledger.create_dlp_rule(
            kind=kind,
            scanner=body.get("scanner"),
            entity_type=str(body.get("entity_type") or ""),
            match_text=body.get("match_text"),
            note=str(body.get("note") or ""),
            user_id=ctx["user_id"],
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if row.get("error") == "duplicate":
        return JSONResponse(
            {"error": "a rule with this scope already exists"}, status_code=409
        )
    return row


@app.post("/api/dlp-rules/reapply")
def api_dlp_rules_reapply(session: str | None = Cookie(None)):
    """Sweep every OPEN alert through the current allowlist. Fully
    suppressed alerts flip to status='allowlisted'; partials are
    counted but left in place (rewriting their findings would require
    recomputing dedup_hash, see dlp.reapply_allowlist_to_open_alerts)."""
    ctx, err = _admin_required(session)
    if err:
        return err
    try:
        result = dlp.reapply_allowlist_to_open_alerts()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return result


@app.delete("/api/dlp-rules/{rule_id}")
def api_dlp_rules_delete(rule_id: int, session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    if not ledger.delete_dlp_rule(rule_id):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"ok": True, "id": rule_id}


@app.delete("/api/settings/{key}")
def api_settings_reset(key: str, session: str | None = Cookie(None)):
    ctx, err = _admin_required(session)
    if err:
        return err
    if key not in settings_module.SPECS:
        return JSONResponse({"error": "unknown_key"}, status_code=404)

    old_value, _old_source = settings_module.get_with_source(key)
    settings_module.reset(key)
    # After reset, the effective value comes from env/default.
    new_value, new_source = settings_module.get_with_source(key)
    _audit_setting_change(ctx, key, old_value, new_value, "reset")
    return {"key": key, "value": new_value, "source": new_source}


# ---------------------------------------------------------------------------
# SPA shell — served by the separate `kyde-ui` nginx container.
# ---------------------------------------------------------------------------
#
# This dashboard container is a pure JSON/auth backend. It handles the
# server-rendered auth flows (/login, /setup, /change-password) and every
# /api/* route, but the SPA shell and /assets/* are served by `kyde-ui`
# which reverse-proxies /api/ + /login + /setup + /openapi.json back here.
#
# `GET /` only fires when someone bypasses nginx (e.g. curling the
# container directly); return a short note so that case is obvious.


@app.get("/", response_class=HTMLResponse)
def serve_dashboard(request: Request, session: str | None = Cookie(None)):
    root = request.scope.get("root_path", "")
    if not _check_session(session):
        return RedirectResponse(root + "/login", status_code=303)
    return HTMLResponse(
        "<h1>KYDE Gateway — dashboard backend</h1>"
        "<p>This port serves the JSON API. The user interface is hosted by "
        "the <code>kyde-ui</code> container — reach it there instead.</p>",
        status_code=200,
    )


@app.get("/favicon.ico")
def favicon():
    """Prevent 404 noise for favicon requests."""
    return Response(status_code=204)


@app.get("/_stcore/{path:path}")
def stcore_stub(path: str):
    """Absorb stale Streamlit service worker requests after migration."""
    return Response(status_code=204)


@app.websocket("/_stcore/{path:path}")
async def stcore_ws_stub(websocket: WebSocket, path: str):
    """Accept and immediately close stale Streamlit WebSocket connections."""
    await websocket.close(code=1000)


# Register side-module routes. Must come after `app` is fully constructed
# and after the middleware is installed — the `topology` module hangs
# decorators on the already-built `app`.
from . import topology  # noqa: E402, F401  # route registration side effect


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")
