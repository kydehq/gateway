"""
LLM-backed session intent classifier.

Calls an OpenAI-compatible chat endpoint with the session's prompt history
condensed into ~500 tokens and asks for a one-of-N intent label. Result is
cached in `session_intents` so we don't re-classify on every read.

Configuration (env or settings):
  INTENT_CLASSIFIER_URL    - chat-completions URL (e.g. http://upstream/v1/chat/completions)
  INTENT_CLASSIFIER_MODEL  - model name (default: 'gpt-4o-mini')
  INTENT_CLASSIFIER_KEY    - bearer token (optional; whatever the URL expects)

If URL is unset, classify_session() returns None and the dashboard falls
back to the keyword classifier in lib/session-names.ts. This keeps the
tests + offline dev workable without provider credentials.

For tests, monkeypatch `_call_model()` to return a stub.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from . import ledger

log = logging.getLogger(__name__)

# Keep this list aligned with lib/session-names.ts intent buckets so the UI
# styling stays consistent regardless of whether the label came from the LLM
# or the keyword fallback.
INTENT_LABELS = [
    "data_query",
    "code_generation",
    "code_review",
    "research",
    "summarization",
    "debugging",
    "configuration",
    "other",
]

_SYSTEM_PROMPT = (
    "You are a classifier. Output one label from this exact list, then a "
    "confidence number between 0 and 1, separated by a colon. No prose.\n"
    "Labels: " + ", ".join(INTENT_LABELS) + "\n"
    "Example output: code_review:0.83"
)

_MAX_TURNS = 6
_MAX_CHARS_PER_TURN = 400


def _condense(turns: list[dict]) -> str:
    """Pick the first user/assistant turns; trim each to keep request small."""
    out: list[str] = []
    for t in turns[:_MAX_TURNS]:
        role = str(t.get("role", "user"))
        content = str(t.get("content", ""))
        if len(content) > _MAX_CHARS_PER_TURN:
            content = content[:_MAX_CHARS_PER_TURN] + "…"
        out.append(f"{role}: {content}")
    return "\n".join(out)


def _parse_response(text: str) -> tuple[str, float]:
    text = text.strip().split("\n", 1)[0]
    if ":" not in text:
        return ("other", 0.0)
    label, _, conf = text.partition(":")
    label = label.strip().lower()
    try:
        confidence = float(conf.strip())
    except ValueError:
        confidence = 0.0
    if label not in INTENT_LABELS:
        label = "other"
    confidence = max(0.0, min(1.0, confidence))
    return label, confidence


def _config() -> Optional[dict]:
    url = os.environ.get("INTENT_CLASSIFIER_URL")
    if not url:
        return None
    return {
        "url": url,
        "model": os.environ.get("INTENT_CLASSIFIER_MODEL", "gpt-4o-mini"),
        "key": os.environ.get("INTENT_CLASSIFIER_KEY", ""),
    }


def _call_model(turns_text: str, cfg: dict) -> str:
    """One synchronous chat-completions call. Separated so tests can patch
    it without standing up an HTTP fixture."""
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = f"Bearer {cfg['key']}"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": turns_text},
        ],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(cfg["url"], json=payload, headers=headers)
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"]


def classify_session(session_id: str) -> Optional[dict]:
    """Classify one session. Returns {intent, confidence, model} on success,
    None when no classifier is configured. Result is cached in
    `session_intents` keyed by session_id."""
    cfg = _config()
    if cfg is None:
        return None

    entries = ledger.get_session_detail(session_id, limit=20)
    turns: list[dict] = []
    for e in entries:
        for m in e.get("why") or []:
            if m.get("role") in ("user", "assistant", "system"):
                turns.append(m)

    if not turns:
        return None

    text = _condense(turns)
    try:
        raw = _call_model(text, cfg)
    except Exception as exc:
        log.warning("intent classifier call failed for %s: %s", session_id, exc)
        return None

    intent, confidence = _parse_response(raw)
    _store(session_id, intent, confidence, cfg["model"])
    return {"intent": intent, "confidence": confidence, "model": cfg["model"]}


def _store(session_id: str, intent: str, confidence: float, model: str) -> None:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_intents (session_id, intent, confidence,
                                              model, classified_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (session_id) DO UPDATE
                   SET intent        = EXCLUDED.intent,
                       confidence    = EXCLUDED.confidence,
                       model         = EXCLUDED.model,
                       classified_at = EXCLUDED.classified_at
                """,
                (session_id, intent, confidence, model),
            )
        conn.commit()


def get_intent(session_id: str) -> Optional[dict]:
    """Read the cached classification for `session_id`, or None if absent."""
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT intent, confidence, model, classified_at"
                "  FROM session_intents WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "intent": row["intent"],
        "confidence": float(row["confidence"]),
        "model": row["model"],
        "classified_at": row["classified_at"].isoformat(),
    }


def get_intents_for(session_ids: list[str]) -> dict[str, dict]:
    """Bulk lookup for /api/sessions. Returns {session_id: {intent,...}}."""
    if not session_ids:
        return {}
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, intent, confidence, model, classified_at
                  FROM session_intents
                 WHERE session_id = ANY(%s)
                """,
                (session_ids,),
            )
            return {
                r["session_id"]: {
                    "intent": r["intent"],
                    "confidence": float(r["confidence"]),
                    "model": r["model"],
                    "classified_at": r["classified_at"].isoformat(),
                }
                for r in cur.fetchall()
            }
