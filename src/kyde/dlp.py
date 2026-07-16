"""
DLP Scanner Integration — concurrent scanning via dlp-bert and dlp-regex sidecars.

Both DLP services run as HTTP sidecars (defined in docker-compose.yml):
  - dlp-bert:  Flask app at http://dlp-bert:8000 (ML-based classifier)
  - dlp-regex: FastAPI app at http://dlp-regex:8000 (pattern-based regex engine)

This module provides a non-blocking async interface to scan text through both
services in parallel. Findings are stored in the dlp_alerts table.
"""

import asyncio
import json
import os
import time

import httpx
from dataclasses import dataclass, field

from . import dlp_policies, ledger, settings

DLP_BERT_URL = "http://dlp-bert:8000"
DLP_REGEX_URL = "http://dlp-regex:8000"
DLP_TIMEOUT = 5.0  # seconds — keep well below typical LLM latency


def bert_enabled() -> bool:
    """Whether the BERT classifier sidecar is deployed alongside this
    gateway. The starter edition ships regex-only (DLP_BERT_ENABLED=false),
    so the gateway must neither call nor health-check bert — otherwise every
    request pays a fail-open round-trip and the status panel reads
    'unhealthy'. This is a deployment fact, not a runtime knob, so it's read
    from the environment rather than the DB-backed settings."""
    return os.environ.get("DLP_BERT_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


# Thresholds are resolved at scan time via kyde.settings. That gives
# operators a runtime knob (DB override → env var → default) without
# redeploying. The resolver caches for ~5 s so this isn't per-request
# database pressure.
def bert_threshold() -> float:
    return float(settings.get("DLP_BERT_THRESHOLD"))


def regex_threshold() -> float:
    return float(settings.get("DLP_REGEX_THRESHOLD"))


@dataclass
class DlpFinding:
    """Result of a single scanner run."""

    scanner: str  # "bert" | "regex"
    alert: bool  # True if findings were raised
    score: float  # Confidence or severity score 0.0–1.0
    findings: list[dict] = field(default_factory=list)  # Raw findings from scanner
    error: str = ""  # Non-empty if HTTP call failed


def _apply_allowlist(finding: DlpFinding) -> tuple[DlpFinding | None, int]:
    """Filter a finding through `dlp_rules` (kind='allow').

    Returns (filtered_finding, suppressed_count):
      - filtered_finding is None when EVERY inner match was allowlisted
        (the caller should skip upsert entirely).
      - Otherwise it's a new DlpFinding with only the non-allowlisted
        matches and a recomputed max-score.

    Regex matches are checked per-entry (entity_type + matched text);
    BERT findings are checked as a whole on their label since BERT has
    no span to match against individual text.
    """
    if not finding.alert or not finding.findings:
        return finding, 0

    if finding.scanner == "regex":
        kept: list[dict] = []
        suppressed = 0
        for m in finding.findings:
            # Regex matches carry several identifiers — users may have
            # allowlisted by whichever one they saw in the UI or logs.
            candidates = [
                str(m.get("pattern_id") or ""),
                str(m.get("pattern_name") or ""),
                str(m.get("entity_type") or ""),
            ]
            text = str(m.get("matched_value") or m.get("text") or m.get("value") or "")
            if not any(c.strip() for c in candidates):
                kept.append(m)
                continue
            hit = ledger.find_and_bump_allow_rule(finding.scanner, candidates, text)
            if hit is not None:
                suppressed += 1
                continue
            kept.append(m)
        if not kept:
            return None, suppressed
        if suppressed == 0:
            return finding, 0
        new_score = max(
            (float(m.get("confidence", 0.0) or 0.0) for m in kept), default=0.0
        )
        return (
            DlpFinding(
                scanner=finding.scanner,
                alert=True,
                score=new_score,
                findings=kept,
            ),
            suppressed,
        )

    if finding.scanner == "bert":
        label = str(finding.findings[0].get("label") or "").strip()
        if label:
            hit = ledger.find_and_bump_allow_rule(finding.scanner, [label], None)
            if hit is not None:
                return None, 1
        return finding, 0

    # Unknown scanner — pass through unfiltered.
    return finding, 0


async def _check_health(client: httpx.AsyncClient, name: str, url: str) -> dict:
    """Ping a sidecar's /health endpoint. Returns
    {"name": str, "ok": bool, "error": str|None, "latency_ms": int|None}.
    Never raises — callers expect a dict per scanner regardless of state.
    """
    started = time.perf_counter()
    try:
        response = await client.get(f"{url}/health", timeout=DLP_TIMEOUT)
        response.raise_for_status()
        return {
            "name": name,
            "ok": True,
            "error": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except httpx.ConnectError:
        return {
            "name": name,
            "ok": False,
            "error": "connection refused",
            "latency_ms": None,
        }
    except httpx.TimeoutException:
        return {"name": name, "ok": False, "error": "timeout", "latency_ms": None}
    except Exception as e:
        return {"name": name, "ok": False, "error": str(e), "latency_ms": None}


async def health_check() -> dict:
    """Probe both DLP sidecars in parallel and return their health.

    Result shape:
      {
        "ok": bool,                          # all scanners healthy
        "scanners": [
          {"name": "bert"|"regex", "ok": bool, "error": str|None, "latency_ms": int|None},
          ...
        ]
      }
    """
    async with httpx.AsyncClient() as client:
        probes = []
        # Starter edition is regex-only — don't probe (or fail on) bert.
        if bert_enabled():
            probes.append(_check_health(client, "bert", DLP_BERT_URL))
        probes.append(_check_health(client, "regex", DLP_REGEX_URL))
        scanners = await asyncio.gather(*probes)
    return {
        "ok": all(s["ok"] for s in scanners),
        "scanners": list(scanners),
    }


async def _scan_bert(
    client: httpx.AsyncClient, text: str, timeout: float = DLP_TIMEOUT
) -> DlpFinding:
    """
    Call dlp-bert (Flask ML classifier) — POST /scan.
    Response: {"flagged": bool, "label": str, "confidence": float, "action": str, ...}
    """
    try:
        response = await client.post(
            f"{DLP_BERT_URL}/scan",
            json={"text": text},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        return DlpFinding(
            scanner="bert",
            alert=data.get("flagged", False),
            score=data.get("confidence", 0.0),
            findings=[
                {
                    "label": data.get("label"),
                    "confidence": data.get("confidence"),
                    "action": data.get("action"),
                }
            ],
        )
    except httpx.ConnectError:
        msg = "dlp-bert unavailable (connection refused)"
        print(f"  ⚠ DLP [bert]: {msg}")
        return DlpFinding(
            scanner="bert", alert=False, score=0.0, findings=[], error=msg
        )
    except httpx.TimeoutException:
        msg = "dlp-bert unavailable (timeout)"
        print(f"  ⚠ DLP [bert]: {msg}")
        return DlpFinding(
            scanner="bert", alert=False, score=0.0, findings=[], error=msg
        )
    except Exception as e:
        msg = f"dlp-bert error: {e}"
        print(f"  ⚠ DLP [bert]: {msg}")
        return DlpFinding(
            scanner="bert", alert=False, score=0.0, findings=[], error=msg
        )


async def _scan_regex(
    client: httpx.AsyncClient, text: str, timeout: float = DLP_TIMEOUT
) -> DlpFinding:
    """
    Call dlp-regex (FastAPI regex engine) — POST /v1/scan.
    Response: {"total_matches": int, "matches": [...], "highest_severity": str|null, ...}
    """
    try:
        response = await client.post(
            f"{DLP_REGEX_URL}/v1/scan",
            json={"content": text, "payload_type": "text"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        # Restart-detection: dlp-regex stamps every scan response with
        # its process-lifetime boot_id. A change means it restarted
        # empty and the gateway needs to re-push the active set.
        try:
            dlp_policies.observe_boot_id(data.get("boot_id"))
        except Exception as e:
            print(f"  ⚠ DLP [regex]: observe_boot_id failed: {e}")

        total_matches = data.get("total_matches", 0)
        matches = data.get("matches", [])

        # Score: maximum confidence across all matches
        score = max((m.get("confidence", 0.0) for m in matches), default=0.0)

        return DlpFinding(
            scanner="regex",
            alert=total_matches > 0,
            score=score,
            findings=matches,
        )
    except httpx.HTTPStatusError as e:
        # 503 = dlp-regex booted empty and hasn't received our push yet.
        # Treat as transient: no alert, no scary log. The gateway's
        # startup push will land shortly; if it already has and we're
        # still seeing this, the more verbose retry path is the right
        # diagnostic surface (the scheduled re-push from observe_boot_id
        # would have run on the previous scan).
        if e.response.status_code == 503:
            msg = "dlp-regex not ready (no patterns loaded)"
            print(f"  · DLP [regex]: {msg}")
            try:
                dlp_policies.request_recovery_push()
            except Exception as exc:
                print(f"  ⚠ DLP [regex]: recovery push schedule failed: {exc}")
            return DlpFinding(
                scanner="regex", alert=False, score=0.0, findings=[], error=msg
            )
        msg = f"dlp-regex http {e.response.status_code}"
        print(f"  ⚠ DLP [regex]: {msg}")
        return DlpFinding(
            scanner="regex", alert=False, score=0.0, findings=[], error=msg
        )
    except httpx.ConnectError:
        msg = "dlp-regex unavailable (connection refused)"
        print(f"  ⚠ DLP [regex]: {msg}")
        return DlpFinding(
            scanner="regex", alert=False, score=0.0, findings=[], error=msg
        )
    except httpx.TimeoutException:
        msg = "dlp-regex unavailable (timeout)"
        print(f"  ⚠ DLP [regex]: {msg}")
        return DlpFinding(
            scanner="regex", alert=False, score=0.0, findings=[], error=msg
        )
    except Exception as e:
        msg = f"dlp-regex error: {e}"
        print(f"  ⚠ DLP [regex]: {msg}")
        return DlpFinding(
            scanner="regex", alert=False, score=0.0, findings=[], error=msg
        )


async def scan_text(text: str) -> list[DlpFinding]:
    """
    Run text through both DLP scanners concurrently.
    Returns a list of DlpFinding objects (one per scanner).
    Never raises — all errors are captured in the error field.
    """
    if not text or not text.strip():
        # Skip empty scans
        return []

    # Truncate to avoid overwhelming the scanners
    text = text[:8000]

    try:
        async with httpx.AsyncClient() as client:
            tasks = []
            # Starter edition runs regex-only — skip bert entirely.
            if bert_enabled():
                tasks.append(asyncio.create_task(_scan_bert(client, text)))
            tasks.append(asyncio.create_task(_scan_regex(client, text)))
            findings = await asyncio.gather(*tasks)
            return list(findings)
    except Exception as e:
        print(f"  ⚠ DLP scan_text failed: {e}")
        return []


# Per-block size caps. Tool results can carry whole file contents; tool
# args occasionally embed credentials or large payloads. Caps keep the
# stored full_messages from blowing past the per-message 4000-char limit
# enforced in server.py, and bound what the DLP scanner ingests per call.
_TOOL_ARGS_CAP = 500
_TOOL_RESULT_CAP = 1500


def render_content_blocks(content: object) -> str:
    """Flatten a message's `content` into a single auditor-readable string.

    Accepts either a plain string (OpenAI-style) or a list of typed blocks
    (Anthropic-style multimodal + tool-calling). Non-text block types are
    rendered as bracketed tags so the entry-detail dialog and the DLP
    scanner both see *something* in messages that previously looked empty:

      [tool_use: <name>({"k":"v"})]
      [tool_result: <content>]
      [image]
      [document]

    Truncation is per-block; the caller (server.py `_full_messages_context`)
    enforces the per-message 4000-char cap on top.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""

    rendered: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text = block.get("text", "") or ""
            if text:
                rendered.append(text)
        elif btype == "tool_use":
            name = block.get("name") or "?"
            args = block.get("input", {})
            try:
                args_str = json.dumps(args, separators=(",", ":"))
            except (TypeError, ValueError):
                args_str = str(args)
            rendered.append(f"[tool_use: {name}({args_str[:_TOOL_ARGS_CAP]})]")
        elif btype == "tool_result":
            inner = block.get("content", "")
            # tool_result.content can itself be either a string or a list
            # of nested blocks (e.g. when a tool returns multiple chunks).
            if isinstance(inner, list):
                inner_text = " ".join(
                    p.get("text", "") or ""
                    for p in inner
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            else:
                inner_text = str(inner) if inner else ""
            rendered.append(f"[tool_result: {inner_text[:_TOOL_RESULT_CAP]}]")
        elif btype == "image":
            rendered.append("[image]")
        elif btype == "document":
            rendered.append("[document]")
        elif btype:
            # Unknown block type — surface it rather than silently drop.
            rendered.append(f"[{btype}]")
    return " ".join(p for p in rendered if p)


def _extract_text_from_messages(messages: list[dict]) -> str:
    """Extract scannable text from a messages list, including the
    structured parts (tool_use / tool_result / etc.) that the older
    text-only filter used to drop on the floor — those frequently carry
    sensitive content (file paths in tool args, file contents in tool
    results) that DLP needs to see."""
    parts: list[str] = []
    for msg in messages:
        rendered = render_content_blocks(msg.get("content", ""))
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


async def scan_and_store_entry(
    entry_id: str,
    session_id: str,
    seq: int,
    messages: list[dict],
    response_body: dict,
) -> None:
    """
    Scan only what's NEW on this entry (delta vs the prior entry in the
    same session) plus the assistant response. Store any alerts found in
    the dlp_alerts table.

    Why delta-only: LLM APIs are stateless, so every call ships the
    entire conversation. Scanning the full payload on every call meant
    one user-side leak in turn 1 would re-fire the regex/BERT pipelines
    on every subsequent turn — producing dozens of nearly-identical
    alerts (the existing dedup hash also breaks down as the surrounding
    context grows and the scanner picks up incidental new matches).
    Scanning only the appended messages catches every genuine new leak
    exactly once, including a re-occurrence of an earlier secret in a
    later turn.

    Fire-and-forget via asyncio.create_task(); must never raise.
    """
    try:
        # Slice off the prior entry's full_messages length so we only
        # scan what this entry contributed. First entry in a session
        # (or non-session traffic) gets offset=0 and scans everything,
        # which is correct — there's no prior context to dedupe against.
        prior_count = ledger.get_prior_full_messages_length(session_id, seq)
        delta_messages = messages[prior_count:]

        # Extract text from the delta only
        request_text = _extract_text_from_messages(delta_messages)

        # Extract text from response (OpenAI-style response)
        response_text = ""
        try:
            response_text = (
                response_body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except (KeyError, IndexError, TypeError):
            pass

        # Combine for unified scan
        combined_text = request_text + "\n" + response_text
        combined_text = combined_text.strip()

        if not combined_text:
            print(f"  · DLP scan skipped for entry={entry_id[:8]} (empty text)")
            return

        short_id = entry_id[:8]
        print(
            f"  → DLP scan: entry={short_id} "
            f"({len(combined_text)} chars, delta {len(delta_messages)}/"
            f"{len(messages)} msgs)"
        )

        # Scan both sides in parallel
        findings_list = await scan_text(combined_text)

        if not findings_list:
            print(f"  ⚠ DLP scan: entry={short_id} returned no results")
            return

        # Store any alerts (where alert=True, no error, and score ≥ threshold).
        # Resolve once per scan — all findings from this call use the same
        # snapshot even if an admin edits the threshold mid-scan.
        thresholds = {
            "bert": bert_threshold(),
            "regex": regex_threshold(),
        }
        for finding in findings_list:
            tag = f"DLP [{finding.scanner}] entry={short_id}"
            if finding.error:
                # Error was already logged inside the scanner helper
                print(f"  ⚠ {tag} error — see above")
                continue
            if not finding.alert:
                print(f"  · {tag} clean (score={finding.score:.3f})")
                continue
            threshold = thresholds.get(finding.scanner, 0.0)
            if finding.score < threshold:
                print(
                    f"  · {tag} suppressed "
                    f"(score {finding.score:.3f} < threshold {threshold:.3f})"
                )
                continue
            # Allowlist check — lets admins silence known-noisy findings
            # without retuning the scanner or changing patterns on disk.
            try:
                filtered, suppressed = _apply_allowlist(finding)
            except Exception as e:
                # A rule-lookup failure must not break alerting; fall
                # through with the unfiltered finding.
                print(f"  ⚠ {tag} allowlist lookup failed — {e}")
                filtered, suppressed = finding, 0
            if filtered is None:
                print(
                    f"  · {tag} fully allowlisted "
                    f"({suppressed} match{'es' if suppressed != 1 else ''} suppressed)"
                )
                continue
            if suppressed:
                print(
                    f"  · {tag} partially allowlisted "
                    f"({suppressed} of {suppressed + len(filtered.findings)} suppressed)"
                )
            finding = filtered
            try:
                row, is_new = ledger.upsert_dlp_alert(
                    entry_id=entry_id,
                    session_id=session_id,
                    scanner=finding.scanner,
                    score=finding.score,
                    findings=finding.findings,
                )
                if is_new:
                    print(f"  ✓ {tag} alert stored (score={finding.score:.3f})")
                else:
                    first = (row.get("entry_id") or "")[:8]
                    last = (row.get("last_seen_entry_id") or "")[:8]
                    print(
                        f"  ◦ {tag} alert deduped "
                        f"(seen_count={row.get('seen_count')} "
                        f"first={first} last={last})"
                    )
            except Exception as e:
                print(f"  ⚠ {tag} failed to store alert: {e}")

    except Exception as e:
        # Broad catch — never crash the event loop
        print(f"  ⚠ scan_and_store_entry failed: {e}")


# ---------------------------------------------------------------------------
# Retrospective allowlist sweep
# ---------------------------------------------------------------------------


def _mark_alert_allowlisted(alert_uuid: str) -> None:
    """Close an open alert as rule-suppressed. Routes through the triage
    state machine so the close shows up in dlp_alert_events alongside
    manual closes — same structural shape, just actor_kind='system'."""
    from . import dlp_triage

    dlp_triage.transition(
        alert_id=alert_uuid,
        to_status="closed",
        actor_kind="system",
        disposition="allowlisted",
        note="suppressed by reapply-allowlist",
    )


def _update_alert_partial(alert_id: int, kept_findings: list[dict]) -> None:
    """Rewrite an open alert's findings with only the non-suppressed
    matches and recompute its max-score. dedup_hash is NOT touched —
    recomputing it could collide with another open alert via the
    partial unique index on (dedup_hash) WHERE status IN ('new',
    'analysis_in_progress'). The existing hash remains a valid
    identifier for the leak cluster as it was originally detected.
    """
    from psycopg.types.json import Jsonb

    now = time.time()
    new_score = max(
        (float(m.get("confidence", 0.0) or 0.0) for m in kept_findings),
        default=0.0,
    )
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dlp_alerts
                   SET findings   = %s,
                       score      = %s,
                       updated_at = %s
                 WHERE id = %s
                """,
                (Jsonb(kept_findings), new_score, now, alert_id),
            )
        conn.commit()


def reapply_allowlist_to_open_alerts() -> dict:
    """Sweep every OPEN alert through the current allowlist retroactively.

    Returns `{scanned, fully_allowlisted, partially_updated, unchanged}`.

    Semantics — v1.5:
      * `fully_allowlisted`: every inner match matched a rule → alert
        closed with disposition='allowlisted' via dlp_triage.transition,
        pending emails cancelled, close event logged.
      * `partially_updated`: some matches were suppressed, others kept.
        The alert's `findings` JSONB is rewritten with only the kept
        matches and `score` is recomputed as their max confidence.
        `dedup_hash` is deliberately NOT recomputed — a new hash could
        collide with another open alert via the partial unique index
        on (dedup_hash) WHERE status <> 'closed'. The existing hash
        still identifies the original leak cluster.
      * `unchanged`: no rule matched any finding.

    Rule `hit_count`s ARE bumped for every suppressed match here — the
    counter reflects total impact including retrospective sweeps.
    """
    scanned = 0
    fully = 0
    partial = 0
    unchanged = 0

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, alert_id, scanner, findings
                  FROM dlp_alerts
                 WHERE status <> 'closed'
                 ORDER BY id
                """
            )
            rows = list(cur.fetchall())

    for row in rows:
        scanned += 1
        scanner = row["scanner"]
        findings_raw = row["findings"] or []
        findings = findings_raw if isinstance(findings_raw, list) else []

        if scanner == "regex":
            kept_findings: list[dict] = []
            suppressed = 0
            for m in findings:
                candidates = [
                    str(m.get("pattern_id") or ""),
                    str(m.get("pattern_name") or ""),
                    str(m.get("entity_type") or ""),
                ]
                text = str(
                    m.get("matched_value") or m.get("text") or m.get("value") or ""
                )
                if not any(c.strip() for c in candidates):
                    kept_findings.append(m)
                    continue
                hit = ledger.find_and_bump_allow_rule(scanner, candidates, text)
                if hit is not None:
                    suppressed += 1
                else:
                    kept_findings.append(m)
            if suppressed > 0 and not kept_findings:
                _mark_alert_allowlisted(row["alert_id"])
                fully += 1
            elif suppressed > 0:
                # v1.5: filter the suppressed matches out in place.
                _update_alert_partial(row["id"], kept_findings)
                partial += 1
            else:
                unchanged += 1

        elif scanner == "bert":
            label = str(findings[0].get("label") or "").strip() if findings else ""
            if label:
                hit = ledger.find_and_bump_allow_rule(scanner, [label], None)
                if hit is not None:
                    _mark_alert_allowlisted(row["alert_id"])
                    fully += 1
                    continue
            unchanged += 1
        else:
            unchanged += 1

    return {
        "scanned": scanned,
        "fully_allowlisted": fully,
        "partially_updated": partial,
        "unchanged": unchanged,
    }
