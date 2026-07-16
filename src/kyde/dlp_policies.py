"""DLP pattern policies — the gateway-side source of truth.

dlp-regex boots empty; this module loads the bundled YAML pattern files
that ship inside the gateway image, applies the per-tenant disable list
from `dlp_disabled_patterns`, and pushes the resulting active set to
dlp-regex via POST /v1/patterns/replace. Re-pushes happen on:

  * gateway startup (before traffic flows),
  * every admin toggle from the Policies page,
  * detection of a new boot_id in dlp-regex scan responses (it restarted).

Hit counts shown in the UI come from a single aggregate over the
existing dlp_alerts.findings JSONB — counting *findings* rather than
alerts, because that matches what an operator skimming the page is
trying to answer ("which pattern fires the most?").

Manual rule authoring is out of scope for v1; this module exposes only
list + per-pattern toggle + resync.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Lock
from typing import Optional

import httpx
import yaml

from . import ledger

# Same URL the scanner uses; defined here so this module doesn't import
# dlp.py and create a circular dependency.
DLP_REGEX_URL = "http://dlp-regex:8000"

# Where the bundled YAML lands inside the gateway image. The Dockerfile
# COPYs ./dlp-patterns → /app/dlp-patterns. Override via env for tests.
BUNDLED_DIR = Path(os.environ.get("DLP_BUNDLED_PATTERNS_DIR", "/app/dlp-patterns"))

# Push retry envelope for startup. dlp-regex can take a moment to come
# up; we don't want to give up on a transient connection refused.
_STARTUP_RETRIES = 6
_STARTUP_BACKOFF_S = 5.0
_PUSH_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Bundled YAML — loaded once at module import, immutable for process lifetime.
# Changing the bundled set means rebuilding the gateway image.
# ---------------------------------------------------------------------------

# Each entry is the dict shape that POST /v1/patterns/replace accepts —
# i.e. the dlp_regex PatternDefinition payload. We never construct a
# PatternDefinition here (avoiding a hard dep on the dlp-regex package);
# the wire format is the contract.
_BUNDLED: dict[str, dict] = {}
_BUNDLED_LOADED = False
_BUNDLED_LOCK = Lock()


def _load_bundled() -> None:
    """Idempotent one-time load of every YAML in BUNDLED_DIR.

    Tolerant of a missing directory (e.g. unit tests without the volume
    mounted) — leaves _BUNDLED empty so the rest of the module is still
    usable. Logs once on entry so a misconfigured deployment is obvious.
    """
    global _BUNDLED_LOADED
    with _BUNDLED_LOCK:
        if _BUNDLED_LOADED:
            return
        if not BUNDLED_DIR.exists():
            print(f"  ⚠ dlp_policies: bundled dir not found at {BUNDLED_DIR}")
            _BUNDLED_LOADED = True
            return
        loaded = 0
        for yaml_file in sorted(BUNDLED_DIR.glob("*.yaml")):
            try:
                with yaml_file.open() as f:
                    data = yaml.safe_load(f)
            except Exception as e:
                print(f"  ⚠ dlp_policies: failed to read {yaml_file.name}: {e}")
                continue
            source = data.get("source") if isinstance(data, dict) else None
            patterns = data.get("patterns") if isinstance(data, dict) else None
            if not source or not isinstance(patterns, list):
                print(f"  ⚠ dlp_policies: {yaml_file.name} missing source/patterns")
                continue
            for p in patterns:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                # Carry the source forward — the YAML stores it once at
                # the file level but the replace endpoint expects it on
                # every pattern.
                merged = {**p, "source": source}
                _BUNDLED[merged["id"]] = merged
                loaded += 1
        print(
            f"  ✓ dlp_policies: loaded {loaded} bundled patterns " f"from {BUNDLED_DIR}"
        )
        _BUNDLED_LOADED = True


def bundled_patterns() -> dict[str, dict]:
    """All bundled patterns, keyed by id."""
    _load_bundled()
    return _BUNDLED


# ---------------------------------------------------------------------------
# Disabled-row store
# ---------------------------------------------------------------------------


def disabled_ids() -> set[str]:
    """Set of pattern_ids currently muted via dlp_disabled_patterns."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pattern_id FROM dlp_disabled_patterns")
            rows = cur.fetchall()
    return {r["pattern_id"] for r in rows}


def _disable(pattern_id: str, user_id: Optional[int]) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dlp_disabled_patterns (pattern_id, disabled_by)
                VALUES (%s, %s)
                ON CONFLICT (pattern_id) DO NOTHING
                """,
                (pattern_id, user_id),
            )
        conn.commit()


def _enable(pattern_id: str) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM dlp_disabled_patterns WHERE pattern_id = %s",
                (pattern_id,),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Prevention-row store — inverted semantics vs the disabled store: a row
# means the pattern BLOCKS requests inline when the global Policy
# Prevention setting is on. Empty table → detect-only for everything.
# ---------------------------------------------------------------------------


def prevention_ids() -> set[str]:
    """Set of pattern_ids opted into inline prevention (blocking)."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pattern_id FROM dlp_prevention_patterns")
            rows = cur.fetchall()
    return {r["pattern_id"] for r in rows}


def _prevention_enable(pattern_id: str, user_id: Optional[int]) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dlp_prevention_patterns (pattern_id, enabled_by)
                VALUES (%s, %s)
                ON CONFLICT (pattern_id) DO NOTHING
                """,
                (pattern_id, user_id),
            )
        conn.commit()


def _prevention_disable(pattern_id: str) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM dlp_prevention_patterns WHERE pattern_id = %s",
                (pattern_id,),
            )
        conn.commit()


def set_prevention(pattern_id: str, enabled: bool, user_id: Optional[int]) -> dict:
    """Toggle a single pattern's prevention flag. Returns the updated UI
    row. No re-push to dlp-regex — prevention is a gateway-side decision
    filter; the scanner keeps scanning the same active set either way.
    """
    if pattern_id not in bundled_patterns():
        raise ValueError(f"unknown pattern_id: {pattern_id}")

    if enabled:
        _prevention_enable(pattern_id, user_id)
    else:
        _prevention_disable(pattern_id)

    return _single_row(pattern_id)


def set_prevention_bulk(enabled: bool, user_id: Optional[int]) -> dict:
    """Enable or disable prevention for EVERY bundled pattern at once.
    Returns {"updated": <row count after the operation>}. Backs the
    enable-all / disable-all buttons on the Policies page."""
    if enabled:
        ids = list(bundled_patterns().keys())
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO dlp_prevention_patterns (pattern_id, enabled_by)
                    VALUES (%s, %s)
                    ON CONFLICT (pattern_id) DO NOTHING
                    """,
                    [(pid, user_id) for pid in ids],
                )
            conn.commit()
        return {"updated": len(ids)}
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dlp_prevention_patterns")
            deleted = cur.rowcount
        conn.commit()
    return {"updated": deleted}


# ---------------------------------------------------------------------------
# Hit counts — single aggregate over the existing dlp_alerts table.
# ---------------------------------------------------------------------------


def _hit_counts() -> dict[str, dict]:
    """Returns {pattern_id: {"hits": int, "last_hit_at": iso|None}}.

    Counts findings (not alerts). One noisy regex matching three values
    inside one payload is three hits, which matches what an operator
    reading the Policies page wants to see when prioritising mutes.
    """
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT finding->>'pattern_id' AS pattern_id,
                       COUNT(*)               AS hits,
                       MAX(created_at)        AS last_hit
                FROM   dlp_alerts,
                       jsonb_array_elements(findings) AS finding
                WHERE  scanner = 'regex'
                   AND finding ? 'pattern_id'
                GROUP  BY 1
                """)
            rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        pid = r["pattern_id"]
        if not pid:
            continue
        last = r["last_hit"]
        if last is None:
            last_iso: Optional[str] = None
        elif hasattr(last, "isoformat"):
            last_iso = last.isoformat()
        else:
            from datetime import datetime, timezone

            last_iso = datetime.fromtimestamp(float(last), tz=timezone.utc).isoformat()
        out[pid] = {
            "hits": int(r["hits"] or 0),
            "last_hit_at": last_iso,
        }
    return out


# ---------------------------------------------------------------------------
# UI assembly
# ---------------------------------------------------------------------------


def list_for_ui() -> list[dict]:
    """List shape consumed by GET /api/dlp-policies.

    One row per bundled pattern. Disabled patterns are still listed so
    the operator can re-enable them — the UI shows them with the switch
    off. Sort is by (source, id) so the frontend's per-source grouping
    lands in a stable order without re-sorting.
    """
    bundled = bundled_patterns()
    disabled = disabled_ids()
    prevention = prevention_ids()
    hits = _hit_counts()

    items: list[dict] = []
    for pid, p in bundled.items():
        h = hits.get(pid, {})
        items.append(
            {
                "id": pid,
                "name": p.get("name") or pid,
                "source": p.get("source") or "unknown",
                "category": p.get("category") or "",
                "severity": p.get("severity") or "",
                "pattern": p.get("pattern") or "",
                "description": p.get("description") or "",
                "enabled": pid not in disabled,
                "prevention": pid in prevention,
                "hits": h.get("hits", 0),
                "last_hit_at": h.get("last_hit_at"),
            }
        )
    items.sort(key=lambda i: (i["source"], i["id"]))
    return items


def active_set() -> list[dict]:
    """Bundled patterns minus the disabled rows. Wire shape for /replace."""
    bundled = bundled_patterns()
    disabled = disabled_ids()
    return [p for pid, p in bundled.items() if pid not in disabled]


# ---------------------------------------------------------------------------
# Push to dlp-regex + boot_id tracking
# ---------------------------------------------------------------------------

# Tracks the boot_id we last successfully pushed to. If a scan response
# carries a different boot_id, dlp-regex restarted and forgot its set —
# observe_boot_id() schedules a re-push.
_LAST_BOOT_ID: Optional[str] = None
_BOOT_LOCK = Lock()


def _set_last_boot_id(boot_id: Optional[str]) -> None:
    global _LAST_BOOT_ID
    with _BOOT_LOCK:
        _LAST_BOOT_ID = boot_id


def _get_last_boot_id() -> Optional[str]:
    with _BOOT_LOCK:
        return _LAST_BOOT_ID


async def push_active_set() -> dict:
    """POST /v1/patterns/replace with the current active set. Returns the
    parsed response dict: {"loaded": int, "boot_id": str}. Caller is
    responsible for handling httpx errors — the startup path swallows
    them with retries; the toggle path lets them bubble to the operator
    so a misconfigured deployment is visible in the UI.
    """
    patterns = active_set()
    async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT_S) as client:
        response = await client.post(
            f"{DLP_REGEX_URL}/v1/patterns/replace",
            json={"patterns": patterns},
        )
        response.raise_for_status()
        body = response.json()
    _set_last_boot_id(body.get("boot_id"))
    return body


async def push_active_set_with_retries() -> Optional[dict]:
    """Startup-time push that tolerates dlp-regex still booting.

    Logs each failure but never raises — if every attempt fails the
    gateway still starts (scans will return 503 from dlp-regex, which is
    the honest signal). Returns the last successful response or None.
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            body = await push_active_set()
            print(
                f"  ✓ dlp_policies: pushed {body.get('loaded')} patterns "
                f"to dlp-regex (boot_id={body.get('boot_id')})"
            )
            return body
        except Exception as e:
            last_err = e
            print(
                f"  ⚠ dlp_policies: push attempt {attempt}/"
                f"{_STARTUP_RETRIES} failed: {e}"
            )
            if attempt < _STARTUP_RETRIES:
                await asyncio.sleep(_STARTUP_BACKOFF_S)
    print(
        f"  ⚠ dlp_policies: giving up after {_STARTUP_RETRIES} attempts; "
        f"last error: {last_err}"
    )
    return None


def observe_boot_id(boot_id: Optional[str]) -> None:
    """Called from the scan path with each /v1/scan response's boot_id.

    First observation seeds the tracker. Any subsequent change means
    dlp-regex restarted — fire a background re-push and don't block the
    current scan (the request that observed the new id is fine: dlp-regex
    accepted it because the prior boot must have been pushed to, so the
    new boot only forgot what to do *next*; the user's response already
    came back with whatever pattern_count the new boot has).
    """
    if not boot_id:
        return
    last = _get_last_boot_id()
    if last is None:
        _set_last_boot_id(boot_id)
        return
    if boot_id == last:
        return
    # Optimistically set so concurrent observers don't all fire pushes.
    _set_last_boot_id(boot_id)
    print(
        f"  ↻ dlp_policies: boot_id changed ({last} → {boot_id}); "
        f"re-pushing active set"
    )
    try:
        asyncio.get_running_loop().create_task(_safe_push())
    except RuntimeError:
        # No running loop — caller is sync code. Run in a fresh loop.
        asyncio.run(_safe_push())


async def _safe_push() -> None:
    try:
        await push_active_set()
    except Exception as e:
        print(f"  ⚠ dlp_policies: drift re-push failed: {e}")


_RECOVERY_LOCK = Lock()
_RECOVERY_IN_FLIGHT = False


def request_recovery_push() -> None:
    """Schedule a push to dlp-regex if one isn't already in flight.

    Called from the scan path's 503 handler. Without this, a restarted
    dlp-regex that we never observe a *successful* scan from would stay
    empty forever — the boot_id-drift path only triggers when /v1/scan
    returns 200 with a new id. Debounced so a burst of scans doesn't
    spawn N concurrent pushes.
    """
    global _RECOVERY_IN_FLIGHT
    with _RECOVERY_LOCK:
        if _RECOVERY_IN_FLIGHT:
            return
        _RECOVERY_IN_FLIGHT = True

    async def _run() -> None:
        global _RECOVERY_IN_FLIGHT
        try:
            await push_active_set()
        except Exception as e:
            print(f"  ⚠ dlp_policies: recovery push failed: {e}")
        finally:
            with _RECOVERY_LOCK:
                _RECOVERY_IN_FLIGHT = False

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Toggle entry point used by the dashboard endpoint
# ---------------------------------------------------------------------------


async def set_enabled(pattern_id: str, enabled: bool, user_id: Optional[int]) -> dict:
    """Toggle a pattern and re-push the active set in one shot.

    Returns the updated UI row for the toggled pattern. Raises ValueError
    if the pattern_id isn't in the bundled set — the UI shouldn't be
    able to send IDs it didn't get from us, but we surface the error
    rather than silently writing a row for a nonexistent pattern.
    """
    if pattern_id not in bundled_patterns():
        raise ValueError(f"unknown pattern_id: {pattern_id}")

    if enabled:
        _enable(pattern_id)
    else:
        _disable(pattern_id, user_id)

    # Re-push synchronously so the UI's invalidate cycle picks up a
    # dlp-regex that's already in the new state.
    await push_active_set()

    # Re-query just enough to return the updated row without doing the
    # full UI assembly again.
    return _single_row(pattern_id)


def _single_row(pattern_id: str) -> dict:
    bundled = bundled_patterns()
    p = bundled[pattern_id]
    disabled = disabled_ids()
    hits = _hit_counts().get(pattern_id, {})
    return {
        "id": pattern_id,
        "name": p.get("name") or pattern_id,
        "source": p.get("source") or "unknown",
        "category": p.get("category") or "",
        "severity": p.get("severity") or "",
        "pattern": p.get("pattern") or "",
        "description": p.get("description") or "",
        "enabled": pattern_id not in disabled,
        "prevention": pattern_id in prevention_ids(),
        "hits": hits.get("hits", 0),
        "last_hit_at": hits.get("last_hit_at"),
    }


def reset_state_for_tests() -> None:
    """Test hook — wipe the boot_id tracker between cases."""
    global _BUNDLED_LOADED
    with _BUNDLED_LOCK:
        _BUNDLED.clear()
        _BUNDLED_LOADED = False
    _set_last_boot_id(None)


# Importable shim so dlp.py's existing scanner can stamp boot_id without
# learning the module's internal name conventions.
__all__ = [
    "bundled_patterns",
    "disabled_ids",
    "prevention_ids",
    "list_for_ui",
    "active_set",
    "push_active_set",
    "push_active_set_with_retries",
    "observe_boot_id",
    "set_enabled",
    "set_prevention",
    "set_prevention_bulk",
]
