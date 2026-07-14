"""
FastAPI proxy — multi-provider endpoint that:
  1. Receives agent requests
  2. Auto-detects the upstream provider from the request path
  3. Forwards them to the real LLM provider
  4. Extracts behavioral signals (tool calls, reasoning context)
  5. Appends a signed entry to the behavioral ledger
  6. Returns the response transparently to the agent

Routing is path-based — no configuration needed:
  /openai/v1/chat/completions   → OpenAI
  /anthropic/v1/messages        → Anthropic
  /gemini/chat/completions      → Gemini
  /copilot/chat/completions     → Copilot
  /v1/chat/completions          → OpenAI (default for unprefixed)
  /v1/messages                  → Anthropic (auto-detected from endpoint)
"""

import asyncio
import json
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount
from starlette.applications import Starlette
from contextlib import asynccontextmanager

from . import ledger
from . import dlp as _dlp
from . import network_origin
from . import _features
from .dashboard import app as dashboard_app
from .config import load_upstreams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Upstream registry: built-in defaults merged with any user config.yaml.
# Custom entries extend the registry; existing entries override the defaults.
# See src/kyde/config.py for the config file format.
UPSTREAMS = load_upstreams()

# How many messages to capture as "why" context
# Keep this small to stay under TPM's 1024-byte buffer for signing
WHY_CONTEXT_MESSAGES = 2


@asynccontextmanager
async def _proxy_lifespan(_app):
    # Push the active DLP regex pattern set to dlp-regex before any agent
    # traffic flows. dlp-regex now boots empty (it's the gateway's job
    # to be the source of truth), so without this call /v1/scan would
    # 503 every request. Tolerates dlp-regex still booting via retries;
    # if every retry fails we still let the proxy start — the scan path
    # gracefully degrades on 503 and observe_boot_id() will re-push as
    # soon as dlp-regex answers.
    try:
        from . import dlp_policies

        await dlp_policies.push_active_set_with_retries()
    except Exception as e:
        print(f"  ⚠ proxy lifespan: dlp_policies push failed — {e}")
    yield


_proxy_app = FastAPI(
    title="Agent Behavioral Ledger Proxy",
    version="0.1.0",
    lifespan=_proxy_lifespan,
)


# ---------------------------------------------------------------------------
# Composite ASGI app — routes /dashboard to the dashboard sub-app BEFORE
# the catch-all proxy route can intercept it.
# ---------------------------------------------------------------------------
app = Starlette(
    routes=[
        Mount("/dashboard", app=dashboard_app),
        Mount("/", app=_proxy_app),
    ],
)

# Public alias for deployments that want to run the proxy on its own port
# (without the dashboard mounted alongside it). Point uvicorn at
# `kyde.server:proxy_app` to get proxy + /health only.
proxy_app = _proxy_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(request: Request) -> str:
    """
    Resolve agent identity from request headers.
    Agents can set X-Agent-ID explicitly.
    Falls back to a hash of the API key (anonymous but consistent).
    Accepts both `Authorization: Bearer <token>` (OpenAI, Claude Code OAuth)
    and `x-api-key: <key>` (Anthropic SDKs). The same key sent either way
    hashes to the same agent_id — the signing contract's algorithm is
    unchanged.
    """
    explicit = request.headers.get("X-Agent-ID")
    if explicit:
        return explicit

    import hashlib

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        key_hash = hashlib.sha256(auth[7:].encode()).hexdigest()[:12]
        return f"agent:{key_hash}"

    api_key = request.headers.get("x-api-key")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        return f"agent:{key_hash}"

    return "agent:unknown"


def _client_ip(request: Request) -> str:
    """Extract caller IP, respecting X-Forwarded-For from a reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Min characters a message needs to be hashed as a correlation anchor.
# Trivial replies ("yes", "thanks") would collide across independent
# conversations — skipping them keeps single-hash matches trustworthy.
_SESSION_MIN_TURN_CHARS = 20


def _extract_text(content) -> str:
    """Flatten a message's `content` field to plain text. Handles the
    two shapes the proxies pass through: a raw string (OpenAI) or a list
    of `{type, text}` parts (Anthropic / multimodal)."""
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        return " ".join(parts)
    return str(content or "")


def _turn_hashes(messages: list[dict]) -> list[str]:
    """Hash each substantive message under (role, content). Short or
    empty turns are skipped so session matches require content unique
    enough to be a reliable correlation anchor."""
    import hashlib

    out: list[str] = []
    for m in messages:
        role = str(m.get("role", "")).strip()
        text = _extract_text(m.get("content", "")).strip()
        if len(text) < _SESSION_MIN_TURN_CHARS:
            continue
        digest = hashlib.sha256(
            f"{role}\n{text}".encode("utf-8", errors="replace")
        ).hexdigest()[:32]
        out.append(digest)
    return out


def _session_id(request: Request, messages: list[dict]) -> tuple[str, list[str]]:
    """Resolve a session id for this request AND return the turn-hashes
    that should be recorded under it once we've processed the response.

    Precedence:
      1. X-Session-ID header (explicit client override).
      2. Thread reconstruction: find any open session sharing at least
         one of this request's substantive turn-hashes, within a 2h
         activity window. Tie-break on most matches, then most recent.
      3. Mint a fresh UUID v4 for a new conversation.

    Conversations with no substantive turns get a fresh UUID too — they
    used to collapse onto a `session:<sha256>` derived from the message,
    which fragmented the ID format. Migration 0002_session_id_normalize.sql
    rewrote that legacy data to UUIDs.

    The second return value is the hash list the caller must pass to
    `ledger.record_session_turns(sid, hashes)` after the response is
    processed. Recording includes the assistant response too (computed
    in the caller), which makes next-turn lookups reliable.
    """
    import uuid

    explicit = request.headers.get("X-Session-ID")
    hashes = _turn_hashes(messages)

    if explicit:
        return explicit, hashes

    if hashes:
        existing = ledger.find_session_by_turn_hashes(hashes)
        if existing:
            return existing, hashes

    # Either no substantive turns or no existing session matched. Mint a
    # fresh UUID v4. Random — we DON'T derive it from the hashes, because
    # two different conversations that happen to open with the same content
    # would otherwise collapse into one.
    return str(uuid.uuid4()), hashes


def _full_messages_context(messages: list[dict]) -> list[dict]:
    """Sanitise and store the complete message history for forensic audit.

    Non-text content blocks (tool_use, tool_result, image, document) are
    rendered into readable bracket-tags by `render_content_blocks` so the
    stored message has something to show in the entry-detail dialog and
    something for the DLP scanner to read. Per-message 4000-char cap
    enforced after rendering."""
    cleaned = []
    for m in messages:
        content = _dlp.render_content_blocks(m.get("content", ""))
        cleaned.append({"role": m.get("role", "?"), "content": content[:4000]})
    return cleaned


def _extract_assistant_text(response_body: dict) -> str:
    """Pull the assistant's text reply out of either an OpenAI
    (`choices[].message.content`) or an Anthropic (`content[]` parts)
    response. Returns "" if the response carried only tool calls / was
    streamed and unavailable."""
    # OpenAI-style
    for choice in response_body.get("choices", []) or []:
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            if any(parts):
                return " ".join(parts)
    # Anthropic-style — top-level `content`
    content = response_body.get("content")
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        if any(parts):
            return " ".join(parts)
    if isinstance(content, str) and content.strip():
        return content
    return ""


def _extract_tool_calls(response_body: dict) -> list[dict]:
    """Pull tool/function calls out of an OpenAI-format response."""
    tool_calls = []
    for choice in response_body.get("choices", []):
        msg = choice.get("message", {})
        for tc in msg.get("tool_calls", []):
            tool_calls.append(
                {
                    "id": tc.get("id"),
                    "function": tc.get("function", {}).get("name"),
                    "args": _safe_parse_args(
                        tc.get("function", {}).get("arguments", "{}")
                    ),
                }
            )
        # Legacy function_call format
        fc = msg.get("function_call")
        if fc:
            tool_calls.append(
                {
                    "id": None,
                    "function": fc.get("name"),
                    "args": _safe_parse_args(fc.get("arguments", "{}")),
                }
            )
    return tool_calls


def _safe_parse_args(args_str: str) -> dict:
    try:
        return json.loads(args_str)
    except Exception:
        return {"raw": args_str}


def _new_anthropic_stream_state() -> dict:
    """Mutable accumulator for an Anthropic SSE stream. Drives
    `_apply_anthropic_sse_chunk` and is consumed by the streaming
    handler when the stream ends."""
    return {
        "content": "",
        "tool_calls": [],
        "tool_buffers": {},
        "usage": {},
    }


def _apply_anthropic_sse_chunk(chunk: dict, state: dict) -> None:
    """Apply a single Anthropic SSE event to the running stream state.

    Anthropic splits a response across event-typed envelopes
    (`message_start`, `content_block_start/delta/stop`, `message_delta`,
    `message_stop`), unlike OpenAI's single `choices[].delta` shape. The
    accumulator captures assistant text from `text_delta` events,
    reassembles `tool_use` blocks from `input_json_delta` events into the
    OpenAI-shape tool_call format that downstream extractors expect, and
    merges token usage (input_tokens on message_start, final
    output_tokens on message_delta).
    """
    ctype = chunk.get("type", "")
    if ctype == "message_start":
        u = (chunk.get("message") or {}).get("usage") or {}
        if "input_tokens" in u:
            state["usage"]["input_tokens"] = u.get("input_tokens", 0)
        if "output_tokens" in u:
            state["usage"]["output_tokens"] = u.get("output_tokens", 0)
        return
    if ctype == "content_block_start":
        idx = chunk.get("index", 0)
        block = chunk.get("content_block") or {}
        if block.get("type") == "tool_use":
            state["tool_buffers"][idx] = {
                "id": block.get("id"),
                "name": block.get("name"),
                "args_raw": "",
            }
        return
    if ctype == "content_block_delta":
        idx = chunk.get("index", 0)
        delta = chunk.get("delta") or {}
        dtype = delta.get("type", "")
        if dtype == "text_delta":
            state["content"] = state["content"] + (delta.get("text") or "")
        elif dtype == "input_json_delta":
            buf = state["tool_buffers"].get(idx)
            if buf is not None:
                buf["args_raw"] += delta.get("partial_json") or ""
        return
    if ctype == "content_block_stop":
        idx = chunk.get("index", 0)
        buf = state["tool_buffers"].pop(idx, None)
        if buf is not None:
            # Round-trip into OpenAI tool_calls shape so the existing
            # _extract_tool_calls path works unchanged for the synthetic
            # response we build at stream end.
            state["tool_calls"].append(
                {
                    "id": buf["id"],
                    "function": {
                        "name": buf["name"],
                        "arguments": buf["args_raw"] or "{}",
                    },
                }
            )
        return
    if ctype == "message_delta":
        u = chunk.get("usage") or {}
        if "output_tokens" in u:
            state["usage"]["output_tokens"] = u.get("output_tokens", 0)
        if "input_tokens" in u:
            state["usage"]["input_tokens"] = u.get("input_tokens", 0)


def _why_context(messages: list[dict]) -> list[dict]:
    """Last N messages, stripped of any binary/large content."""
    context = messages[-WHY_CONTEXT_MESSAGES:] if messages else []
    cleaned = []
    for m in context:
        content = m.get("content", "")
        if isinstance(content, list):
            # Multi-modal — keep text parts only
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = " ".join(text_parts)
        cleaned.append({"role": m.get("role", "?"), "content": str(content)[:150]})
    return cleaned


def _extract_token_usage(response_body: dict) -> tuple[int, int]:
    """Extract prompt (up) and completion (down) token counts from upstream response.

    Works with OpenAI, Anthropic, and Gemini response formats.
    Returns (prompt_tokens, completion_tokens).
    """
    usage = response_body.get("usage", {})
    if not usage:
        return 0, 0
    # OpenAI / Gemini: prompt_tokens, completion_tokens
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return int(prompt), int(completion)


def _action_type(request_body: dict, response_body: dict) -> str:
    tool_calls = _extract_tool_calls(response_body)
    if tool_calls:
        return "tool_call"
    finish = ""
    for choice in response_body.get("choices", []):
        finish = choice.get("finish_reason", "")
    if finish == "stop":
        return "chat"
    return "chat"


# Canonical path-kind buckets used by the agent_traffic_meters table.
# Orthogonal to REQUEST_KIND_* (which classifies *content* of a chat-shaped
# row): path_kind says what kind of API endpoint was hit. The proxy fully
# logs path_kind='chat' today; everything else is metered-only unless an
# operator flips mode='full_logging' for that (agent, path_kind).
PATH_KIND_CHAT = "chat"
PATH_KIND_EMBEDDING = "embedding"
PATH_KIND_MODERATION = "moderation"
PATH_KIND_MODELS_LIST = "models_list"
PATH_KIND_TOKENS_COUNT = "tokens_count"
PATH_KIND_AUDIO_TRANSCRIPTION = "audio_transcription"
PATH_KIND_AUDIO_TRANSLATION = "audio_translation"
PATH_KIND_AUDIO_SPEECH = "audio_speech"
PATH_KIND_IMAGE_GENERATION = "image_generation"
PATH_KIND_IMAGE_EDIT = "image_edit"
PATH_KIND_IMAGE_VARIATION = "image_variation"
PATH_KIND_LEGACY_COMPLETION = "legacy_completion"
PATH_KIND_FILE_OP = "file_op"
PATH_KIND_FINE_TUNING = "fine_tuning"
PATH_KIND_UNKNOWN = "unknown"


def _path_kind(upstream: str, api_path: str) -> str:
    """Classify the upstream + api_path into one of the PATH_KIND_* buckets.

    Pure function — no DB or network. Handles the upstream variations in
    how api_path arrives:
      - OpenAI / Anthropic: v1/ stripped (e.g. 'embeddings', 'chat/completions')
      - Gemini / Copilot / Ollama: v1/ or v1beta/ preserved
      - Gemini also uses ':method' suffixes on /models/{name} URLs

    Anything we can't classify returns PATH_KIND_UNKNOWN — that's the
    operator's signal to add a bucket.
    """
    p = api_path.lstrip("/")
    # Normalise version prefixes so a single match table covers all
    # upstreams. Strip the FIRST version segment only; downstream code
    # may still see another (e.g. Anthropic's '/v1/messages' came in as
    # 'messages' already, no second strip needed).
    for prefix in ("v1beta/", "v1/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break

    # Chat-shaped paths — these are the ones _should_log_path already
    # allow-lists. Keep them returning PATH_KIND_CHAT so the meter and
    # the ledger agree on what "chat" means.
    if p in ("chat/completions", "messages", "api/chat", "api/generate"):
        return PATH_KIND_CHAT

    # Gemini uses /models/{model_name}:method instead of a flat endpoint
    # name. Inspect the action suffix before falling through to models_list.
    if ":generateContent" in p or ":streamGenerateContent" in p:
        return PATH_KIND_CHAT
    if ":embedContent" in p or ":batchEmbedContents" in p:
        return PATH_KIND_EMBEDDING
    if ":countTokens" in p:
        return PATH_KIND_TOKENS_COUNT

    if p == "embeddings":
        return PATH_KIND_EMBEDDING
    if p == "moderations":
        return PATH_KIND_MODERATION
    if p == "completions":
        return PATH_KIND_LEGACY_COMPLETION

    # /models, /models/, /models/{id}, /models/{id}/permissions, etc. all
    # count as the same bucket from a metering perspective.
    if p == "models" or p.startswith("models/") or p.startswith("models?"):
        return PATH_KIND_MODELS_LIST

    if p.startswith("audio/transcriptions"):
        return PATH_KIND_AUDIO_TRANSCRIPTION
    if p.startswith("audio/translations"):
        return PATH_KIND_AUDIO_TRANSLATION
    if p.startswith("audio/speech"):
        return PATH_KIND_AUDIO_SPEECH

    if p.startswith("images/generations"):
        return PATH_KIND_IMAGE_GENERATION
    if p.startswith("images/edits"):
        return PATH_KIND_IMAGE_EDIT
    if p.startswith("images/variations"):
        return PATH_KIND_IMAGE_VARIATION

    if p.startswith("files"):
        return PATH_KIND_FILE_OP
    if p.startswith("fine_tuning") or p.startswith("fine-tuning"):
        return PATH_KIND_FINE_TUNING

    return PATH_KIND_UNKNOWN


# Possible values for the request_kind ledger column. The proxy filters
# non-chat endpoints (_should_log_path), so this enum classifies *why* a
# chat-shaped row has the content it does — rather than what kind of API
# endpoint it hit. Migration 0010_request_kind.sql backfills existing rows.
REQUEST_KIND_CHAT = "chat"
REQUEST_KIND_CHAT_TOOL_ONLY = "chat_tool_only"
REQUEST_KIND_CHAT_STREAMING_PARTIAL = "chat_streaming_partial"
REQUEST_KIND_CHAT_EMPTY_REQUEST = "chat_empty_request"
REQUEST_KIND_CHAT_EMPTY_CONTENT = "chat_empty_content"
REQUEST_KIND_POLICY_BLOCK = "policy_block"
REQUEST_KIND_UNKNOWN = "unknown"


def _request_kind(
    action_type: str,
    messages: list[dict],
    response_body: dict,
    tool_calls: list[dict],
) -> str:
    """Classify a chat-shaped ledger row into one of the REQUEST_KIND_*
    buckets. Pure function of the same inputs ledger.append() already
    receives — kept derivative so we can revise the classifier without
    invalidating signed history (request_kind is NOT in _signable())."""
    if action_type == "policy_block":
        return REQUEST_KIND_POLICY_BLOCK

    assistant_text = _extract_assistant_text(response_body)

    if tool_calls and not assistant_text.strip():
        return REQUEST_KIND_CHAT_TOOL_ONLY

    # Empty request — every "real" message (i.e. non-system) is missing or
    # blank. A buggy client posting messages=[] or only a system prompt
    # lands here. Distinct from chat_empty_content (messages present but
    # carrying empty strings).
    non_system = [m for m in (messages or []) if m.get("role") != "system"]
    if not non_system:
        return REQUEST_KIND_CHAT_EMPTY_REQUEST
    if all(not str(m.get("content", "")).strip() for m in non_system):
        return REQUEST_KIND_CHAT_EMPTY_CONTENT

    # Streaming that came back with neither text nor tool calls — the
    # synthetic response carries _streamed=True (see _handle_streaming).
    # Indicates SSE was interrupted or the upstream returned an empty
    # stream; surface it as a distinct bucket so operators can spot a
    # streaming-capture regression.
    if response_body.get("_streamed") and not tool_calls and not assistant_text.strip():
        return REQUEST_KIND_CHAT_STREAMING_PARTIAL

    return REQUEST_KIND_CHAT


def _resolve_upstream(path: str) -> tuple[str, dict, str]:
    """
    Auto-detect upstream provider from the request path.

    Returns (upstream_name, upstream_config, api_path) where api_path is
    the path to forward to the upstream (with the provider prefix stripped).

    Routing rules:
      /openai/...    → OpenAI     (prefix stripped)
      /anthropic/... → Anthropic  (prefix stripped)
      /claude/...    → Anthropic  (prefix stripped, alias)
      /gemini/...    → Gemini     (prefix stripped)
      /copilot/...   → Copilot    (prefix stripped)
      /v1/messages   → Anthropic  (auto-detected from endpoint)
      /v1/...        → OpenAI     (default for unprefixed)
      /...           → OpenAI     (fallback)
    """
    cleaned = path.lstrip("/")
    # Strip v1/ prefix for detection
    without_v1 = cleaned[3:] if cleaned.startswith("v1/") else cleaned

    # 1. Explicit provider prefix: /openai/v1/chat/completions
    #    Also matches /v1/openai/chat/completions (v1 before provider).
    #    Strips both the provider name and any v1/ to avoid doubling
    #    with the upstream config's api_prefix.
    for provider in list(UPSTREAMS.keys()) + ["claude"]:
        prefix = provider + "/"
        # Check raw path first, then v1-stripped path
        for candidate in (cleaned, without_v1):
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix) :]
                name = "anthropic" if provider == "claude" else provider
                # Strip leading v1/ only when the upstream will re-add /v1
                # via api_prefix (OpenAI, Anthropic). For upstreams whose
                # api_prefix is empty (Gemini, Copilot, Ollama) the client's
                # path is forwarded verbatim — so /ollama/v1/chat/completions
                # reaches Ollama's OpenAI-compat endpoint while
                # /ollama/api/chat stays on the native namespace.
                if UPSTREAMS[name].get("api_prefix", "").strip("/") == "v1":
                    while remainder.startswith("v1/"):
                        remainder = remainder[3:]
                return name, UPSTREAMS[name], remainder

    # 2. Auto-detect from endpoint name
    # Anthropic uses /v1/messages, OpenAI uses /v1/chat/completions
    if without_v1 == "messages" or without_v1.startswith("messages/"):
        return "anthropic", UPSTREAMS["anthropic"], without_v1

    # 3. Default to OpenAI for everything else
    return "openai", UPSTREAMS["openai"], without_v1


def _build_upstream_url(upstream_config: dict, api_path: str) -> str:
    base = upstream_config["base"].rstrip("/")
    prefix = upstream_config.get("api_prefix", "").rstrip("/")
    suffix = api_path.lstrip("/")
    if prefix:
        return f"{base}{prefix}/{suffix}"
    return f"{base}/{suffix}"


def _decompress_body(body: bytes, content_encoding: str) -> bytes:
    """Decompress request body for parsing. Forward original bytes to upstream."""
    enc = content_encoding.lower().strip()
    if not body or not enc:
        return body
    try:
        if enc == "gzip":
            import gzip

            return gzip.decompress(body)
        if enc in ("zstd", "zstandard"):
            import zstandard

            return zstandard.ZstdDecompressor().decompress(body)
        if enc == "br":
            import brotli

            return brotli.decompress(body)
        if enc == "deflate":
            import zlib

            return zlib.decompress(body)
    except Exception:
        pass
    return body


def _should_log_path(api_path: str) -> bool:
    # Some upstreams (empty api_prefix: Gemini, Copilot, Ollama) have their
    # v1/ preserved in api_path, while others (OpenAI, Anthropic) have it
    # stripped. Normalise before comparison.
    p = api_path.lstrip("/")
    if p.startswith("v1/"):
        p = p[3:]
    return p in (
        "chat/completions",  # OpenAI, Gemini, Copilot, Ollama OpenAI-compat
        "messages",  # Anthropic
        "api/chat",  # Ollama native chat
        "api/generate",  # Ollama native generate
    )


def _should_log_for_agent(agent_id: str, path_kind: str) -> bool:
    """Phase B2 logging gate. Chat-shaped paths always log (current
    behavior preserved). Non-chat paths log only when an operator has
    flipped mode='full_logging' for this (agent_id, path_kind). The
    cached read keeps this cheap on the hot path; a flip via the
    dashboard endpoint invalidates the local entry, and the 5s TTL
    catches up multi-proxy deployments without a pub/sub channel.
    """
    if path_kind == PATH_KIND_CHAT:
        return True
    mode = ledger.get_agent_traffic_mode_cached(agent_id, path_kind)
    return mode == ledger.TRAFFIC_MODE_FULL_LOGGING


# Ollama's native API uses shapes that differ from OpenAI's. To keep the
# ledger schema uniform, we normalise both the request and response into
# an OpenAI-shaped dict before handing them to `_log_entry`. Helpers below
# are no-ops for api_paths that already use the OpenAI shape.


def _normalize_request_messages(api_path: str, request_body: dict) -> list[dict]:
    if api_path == "api/generate":
        prompt = request_body.get("prompt", "")
        return [{"role": "user", "content": str(prompt)}] if prompt else []
    return request_body.get("messages", [])


def _merge_ndjson_chunks(api_path: str, body_text: str) -> dict | None:
    """Collapse an Ollama NDJSON stream body into a single synthetic response
    suitable for feeding into `_normalize_response`. Returns None if parsing
    fails or the path is not Ollama-native."""
    lines = [ln for ln in body_text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        chunks = [json.loads(ln) for ln in lines]
    except Exception:
        return None
    last = chunks[-1] if chunks else {}
    if api_path == "api/chat":
        accumulated = "".join(
            (c.get("message") or {}).get("content", "") for c in chunks
        )
        last_msg = last.get("message") or {}
        return {
            **last,
            "message": {
                "role": last_msg.get("role", "assistant"),
                "content": accumulated,
                "tool_calls": last_msg.get("tool_calls", []),
            },
        }
    if api_path == "api/generate":
        accumulated = "".join(c.get("response", "") for c in chunks)
        return {**last, "response": accumulated}
    return None


def _normalize_response(api_path: str, response_body: dict) -> dict:
    if not isinstance(response_body, dict):
        return response_body
    if api_path == "api/chat":
        msg = response_body.get("message") or {}
        return {
            "choices": [
                {
                    "message": {
                        "role": msg.get("role", "assistant"),
                        "content": msg.get("content", ""),
                        "tool_calls": msg.get("tool_calls", []),
                    },
                    "finish_reason": response_body.get("done_reason") or "stop",
                }
            ],
            "model": response_body.get("model", "unknown"),
            "usage": {
                "prompt_tokens": response_body.get("prompt_eval_count", 0),
                "completion_tokens": response_body.get("eval_count", 0),
            },
        }
    if api_path == "api/generate":
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": response_body.get("response", ""),
                        "tool_calls": [],
                    },
                    "finish_reason": response_body.get("done_reason") or "stop",
                }
            ],
            "model": response_body.get("model", "unknown"),
            "usage": {
                "prompt_tokens": response_body.get("prompt_eval_count", 0),
                "completion_tokens": response_body.get("eval_count", 0),
            },
        }
    return response_body


# ---------------------------------------------------------------------------
# MCP routing — JSON-RPC over Streamable HTTP. Registered BEFORE the
# catch-all proxy route below so /mcp/... isn't swallowed as an upstream
# chat-completions path. See src/kyde/mcp_proxy.py.
# ---------------------------------------------------------------------------


from . import mcp_aggregator as _mcp_aggregator  # noqa: E402
from . import mcp_proxy as _mcp_proxy  # noqa: E402  (placed here for routing order)


# M4: bare /mcp aggregator. Declared BEFORE /mcp/{server_name} so the
# exact-match wins — FastAPI routes are matched in declaration order.
@_proxy_app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
@_proxy_app.api_route("/mcp/", methods=["GET", "POST", "DELETE"])
async def mcp_aggregator_route(request: Request):
    return await _mcp_aggregator.handle_aggregator_request(request)


@_proxy_app.api_route("/mcp/{server_name}", methods=["GET", "POST", "DELETE"])
async def mcp_route(request: Request, server_name: str):
    return await _mcp_proxy.handle_mcp_request(server_name, request)


# ---------------------------------------------------------------------------
# Proxy route — path-based auto-detection of upstream provider
# ---------------------------------------------------------------------------


@_proxy_app.api_route(
    "/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
@_proxy_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str):
    # Strip leading v1/ and detect upstream from path
    upstream_name, upstream_config, api_path = _resolve_upstream(path)

    # Keep explicit health route behavior if catch-all route receives /health.
    if api_path == "health":
        return await health()

    # ---- Block-list enforcement ------------------------------------------
    # Resolve the agent identity early so a blocked agent is rejected before
    # we forward the request to the upstream. Log the rejection as
    # action_type='policy_block' so the audit trail records the prevention.
    agent_id = _agent_id(request)

    # ---- Traffic metering (Phase B1) ------------------------------------
    # Always increment the (agent_id, path_kind) counter — including for
    # requests that get blocked below, including for non-chat endpoints
    # the ledger doesn't fully log. Best-effort: a transient DB error
    # here must not affect proxy behavior.
    path_kind = _path_kind(upstream_name, api_path)
    try:
        ledger.record_agent_traffic(agent_id, path_kind)
    except Exception as _meter_err:
        print(f"  ⚠ traffic meter UPSERT failed: {_meter_err}")

    # Block-list enforcement is an enterprise feature (kyde/enforce). In the
    # sandbox edition the package is absent, so this guard is skipped and
    # no agent is ever blocked.
    if _features.HAS_ENFORCEMENT and _features.enforce.is_agent_blocked(agent_id):
        return _features.enforce.serve_agent_block(
            agent_id=agent_id,
            path=path,
            method=request.method,
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            upstream_name=upstream_name,
        )

    # ---- Read request body ------------------------------------------------
    body_bytes = await request.body()
    try:
        parse_bytes = _decompress_body(
            body_bytes, request.headers.get("content-encoding", "")
        )
        request_body = json.loads(parse_bytes) if parse_bytes else {}
    except (json.JSONDecodeError, Exception):
        request_body = {}

    # ---- Inline DLP prevention (request-side) ------------------------------
    # Only chat-shaped paths carry messages, and the gate is two cached
    # settings reads when prevention is off. Blocking happens BEFORE the
    # upstream forward — including for stream=true requests, which get the
    # JSON 403 instead of an SSE stream. Any internal error is fail-open:
    # prevention must never take the proxy down.
    if (
        _features.HAS_ENFORCEMENT
        and path_kind == PATH_KIND_CHAT
        and _features.enforce.is_active()
    ):
        try:
            prevention_messages = _normalize_request_messages(api_path, request_body)
            if prevention_messages:
                session_id, _ = _session_id(request, prevention_messages)
                decision = await _features.enforce.evaluate_request(
                    prevention_messages, session_id
                )
                if decision is not None:
                    return _features.enforce.serve_dlp_block(
                        agent_id=_agent_id(request),
                        request_body=request_body,
                        why_messages=_why_context(prevention_messages),
                        full_ctx=_full_messages_context(prevention_messages),
                        client_ip=_client_ip(request),
                        user_agent=request.headers.get("user-agent", ""),
                        session_id=session_id,
                        upstream_name=upstream_name,
                        decision=decision,
                    )
        except Exception as _prev_err:
            print(f"  ⚠ inline DLP prevention errored (fail-open): {_prev_err}")

    print(f"  → Proxy request: {request.method} /{path} → {upstream_name}/{api_path}")

    # ---- Build forwarded headers ------------------------------------------
    forward_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "x-agent-id")
    }

    # ---- Forward to upstream ----------------------------------------------
    upstream_url = _build_upstream_url(upstream_config, api_path)

    # Handle streaming — pass through but buffer for logging
    stream_requested = request_body.get("stream", False)

    if stream_requested:
        return await _handle_streaming(
            upstream_url,
            forward_headers,
            body_bytes,
            request_body,
            request,
            api_path,
            upstream_name,
        )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.request(
                method=request.method,
                url=upstream_url,
                headers=forward_headers,
                content=body_bytes,
            )
    except httpx.TimeoutException as _exc:
        # Catch the timeout subclass before the broader RequestError below.
        if _should_log_for_agent(agent_id, path_kind):
            _log_error_entry(
                request=request,
                request_body=request_body,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
                path_kind=path_kind,
                agent_id=agent_id,
                error_kind="error_timeout",
                status_code=504,
                detail=f"upstream timeout: {_exc}",
            )
        return JSONResponse(
            status_code=504,
            content={
                "error": {"message": "upstream timeout", "type": "gateway_timeout"}
            },
        )
    except httpx.RequestError as _exc:
        # Connection refused, DNS failure, read error, etc.
        if _should_log_for_agent(agent_id, path_kind):
            _log_error_entry(
                request=request,
                request_body=request_body,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
                path_kind=path_kind,
                agent_id=agent_id,
                error_kind="error_upstream",
                status_code=502,
                detail=f"upstream request error: {_exc}",
            )
        return JSONResponse(
            status_code=502,
            content={
                "error": {"message": "upstream unavailable", "type": "bad_gateway"}
            },
        )

    # ---- Parse response ---------------------------------------------------
    content_type = response.headers.get("content-type", "").lower()

    # Ollama's native /api/chat and /api/generate stream NDJSON by default,
    # even when the request body omits `stream` (the server's default is
    # stream=true, not the client's). Detect that, merge the chunks into a
    # synthetic single response for the ledger, and forward the raw NDJSON
    # bytes back so the Ollama SDK still sees the format it expects.
    if "ndjson" in content_type:
        if _should_log_path(api_path) and response.status_code == 200:
            merged = _merge_ndjson_chunks(api_path, response.text)
            if merged is not None:
                messages = _normalize_request_messages(api_path, request_body)
                normalized_response = _normalize_response(api_path, merged)
                _log_entry(
                    request=request,
                    request_body=request_body,
                    response_body=normalized_response,
                    messages=messages,
                    upstream_name=upstream_name,
                    upstream_url=upstream_url,
                )
        elif _should_log_path(api_path) and response.status_code != 200:
            _log_error_entry(
                request=request,
                request_body=request_body,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
                path_kind=path_kind,
                agent_id=agent_id,
                error_kind=_http_error_kind(response.status_code),
                status_code=response.status_code,
                detail=response.text[:500],
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=content_type,
        )

    try:
        response_body = response.json()
    except Exception:
        # Non-JSON response (e.g. embeddings binary) — pass through. A non-200
        # here is still a failed outcome worth counting for reliability.
        if response.status_code != 200 and _should_log_for_agent(agent_id, path_kind):
            _log_error_entry(
                request=request,
                request_body=request_body,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
                path_kind=path_kind,
                agent_id=agent_id,
                error_kind=_http_error_kind(response.status_code),
                status_code=response.status_code,
                detail=response.text[:500],
            )
        return JSONResponse(
            status_code=response.status_code,
            content=response.text,
        )

    # ---- Log to behavioral ledger ----------------------------------------
    # Chat-shaped paths always log (today's behavior). Non-chat paths log
    # only when the operator has flipped this (agent, path_kind) tuple to
    # 'full_logging' via the Traffic Inventory UI. _should_log_for_agent
    # consults the cached mode so the hot path stays cheap.
    if _should_log_for_agent(agent_id, path_kind):
        if response.status_code == 200:
            if path_kind == PATH_KIND_CHAT:
                messages = _normalize_request_messages(api_path, request_body)
                normalized_response = _normalize_response(api_path, response_body)
                _log_entry(
                    request=request,
                    request_body=request_body,
                    response_body=normalized_response,
                    messages=messages,
                    upstream_name=upstream_name,
                    upstream_url=upstream_url,
                )
            else:
                _log_non_chat_entry(
                    request=request,
                    request_body=request_body,
                    response_body=response_body,
                    upstream_name=upstream_name,
                    upstream_url=upstream_url,
                    path_kind=path_kind,
                    agent_id=agent_id,
                )
        else:
            # Non-200 upstream response — record the failure so reliability
            # counts it. Symmetric with the success gate above.
            _log_error_entry(
                request=request,
                request_body=request_body,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
                path_kind=path_kind,
                agent_id=agent_id,
                error_kind=_http_error_kind(response.status_code),
                status_code=response.status_code,
                detail=response_body,
            )

    return JSONResponse(status_code=response.status_code, content=response_body)


# _serve_dlp_block moved to kyde/enforce/handler.py (serve_dlp_block) —
# it is enterprise enforcement code and must be absent from the sandbox image.


async def _handle_streaming(
    upstream_url,
    forward_headers,
    body_bytes,
    request_body,
    request,
    path,
    upstream_name="",
):
    """Stream response to client while buffering for ledger logging."""
    chunks = []
    accumulated_content = ""
    accumulated_tool_calls = []
    # Ollama streams newline-delimited JSON rather than OpenAI-style SSE.
    is_ollama_native = path in ("api/chat", "api/generate")
    # Anthropic SSE uses event-typed envelopes (message_start, content_block_*,
    # message_delta) instead of OpenAI's `choices[].delta` shape. The proxy
    # forwards bytes verbatim either way, but the accumulator needs the right
    # parser so the ledger captures assistant text + tool calls.
    is_anthropic_sse = upstream_name == "anthropic"
    anthropic_state = _new_anthropic_stream_state() if is_anthropic_sse else None
    # Tracks whether the stream failed (non-200 open or mid-stream interruption)
    # so the post-stream logging records an error row instead of a synthetic
    # success — and never both for the same failed request.
    stream_failed = False

    async def event_stream():
        nonlocal accumulated_content, accumulated_tool_calls, stream_failed

        try:
            async with (
                httpx.AsyncClient(timeout=120.0) as client,
                client.stream(
                    method=request.method,
                    url=upstream_url,
                    headers=forward_headers,
                    content=body_bytes,
                ) as upstream_response,
            ):
                if upstream_response.status_code != 200:
                    stream_failed = True
                    print(
                        f"  ⚠ Upstream returned {upstream_response.status_code} "
                        f"on streaming request"
                    )
                    if _should_log_path(path):
                        _log_error_entry(
                            request=request,
                            request_body=request_body,
                            upstream_name=upstream_name,
                            upstream_url=upstream_url,
                            path_kind=PATH_KIND_CHAT,
                            agent_id=_agent_id(request),
                            error_kind=_http_error_kind(upstream_response.status_code),
                            status_code=upstream_response.status_code,
                            detail="upstream error on streaming request",
                        )
                async for line in upstream_response.aiter_lines():
                    # Forward every line (preserves event: fields for Anthropic SSE)
                    yield f"{line}\n"
                    if is_ollama_native:
                        # Each line is a standalone JSON object.
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        chunks.append(chunk)
                        if path == "api/chat":
                            msg = chunk.get("message") or {}
                            accumulated_content += msg.get("content") or ""
                            for tc in msg.get("tool_calls", []):
                                accumulated_tool_calls.append(tc)
                        else:  # api/generate
                            accumulated_content += chunk.get("response") or ""
                        continue
                    # OpenAI / Anthropic SSE format
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except Exception:
                            continue
                        chunks.append(chunk)
                        if is_anthropic_sse:
                            _apply_anthropic_sse_chunk(chunk, anthropic_state)
                        else:
                            for choice in chunk.get("choices", []):
                                delta = choice.get("delta", {})
                                accumulated_content += delta.get("content") or ""
                                for tc in delta.get("tool_calls", []):
                                    accumulated_tool_calls.append(tc)
        except httpx.RemoteProtocolError as e:
            print(f"  ⚠ Stream interrupted by upstream: {e}")
            stream_failed = True
            if _should_log_path(path):
                _log_error_entry(
                    request=request,
                    request_body=request_body,
                    upstream_name=upstream_name,
                    upstream_url=upstream_url,
                    path_kind=PATH_KIND_CHAT,
                    agent_id=_agent_id(request),
                    error_kind="error_stream",
                    status_code=502,
                    detail=f"stream interrupted: {e}",
                )
            yield "data: [DONE]\n"

        if is_anthropic_sse:
            # Promote the Anthropic accumulator's final values into the same
            # variables the synthetic response is built from below.
            accumulated_content = anthropic_state["content"]
            accumulated_tool_calls = list(anthropic_state["tool_calls"])

        # Log after stream completes — but skip the synthetic success row if the
        # stream failed (a non-200 open or mid-stream interruption already wrote
        # an error row above).
        if _should_log_path(path) and not stream_failed:
            # Reconstruct a synthetic response body for logging.
            stream_usage = {}
            if is_anthropic_sse and anthropic_state["usage"]:
                # _extract_token_usage already understands input_tokens /
                # output_tokens, so no renaming needed here.
                stream_usage = dict(anthropic_state["usage"])
            elif is_ollama_native:
                for ch in reversed(chunks):
                    if ch.get("done"):
                        stream_usage = {
                            "prompt_tokens": ch.get("prompt_eval_count", 0),
                            "completion_tokens": ch.get("eval_count", 0),
                        }
                        break
            else:
                for ch in reversed(chunks):
                    if ch.get("usage"):
                        stream_usage = ch["usage"]
                        break
            synthetic_response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": accumulated_content,
                            "tool_calls": accumulated_tool_calls,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": request_body.get("model", "unknown"),
                "usage": stream_usage,
                "_streamed": True,
            }
            messages = _normalize_request_messages(path, request_body)
            _log_entry(
                request=request,
                request_body=request_body,
                response_body=synthetic_response,
                messages=messages,
                upstream_name=upstream_name,
                upstream_url=upstream_url,
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _log_non_chat_entry(
    request,
    request_body,
    response_body,
    upstream_name: str,
    upstream_url: str,
    path_kind: str,
    agent_id: str,
):
    """Phase B2: append a minimal ledger row for a non-chat path that an
    operator has flipped to mode='full_logging'.

    Differs from _log_entry in three ways:
    - No session / why / full_messages / tool_calls (those are chat
      concepts and don't apply to embeddings / models-list / etc).
    - action_type is 'api_call', not 'chat' — so action_type filters in
      the audit log don't accidentally lump embeddings in with chat.
    - request_kind is the path_kind itself (e.g., 'embedding',
      'models_list') rather than one of the chat content classifiers.
      The input_hash and output_hash still cover the full bodies so
      forensic integrity holds.

    Best-effort: any failure is logged and swallowed, same contract as
    _log_entry — a ledger hiccup must never propagate to the proxy path.
    """
    try:
        client_ip = _client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:500]
        model = (
            (request_body.get("model") if isinstance(request_body, dict) else None)
            or (response_body.get("model") if isinstance(response_body, dict) else None)
            or ""
        )
        prompt_tokens, completion_tokens = _extract_token_usage(response_body)

        entry = ledger.append(
            agent_id=agent_id,
            action_type="api_call",
            model=model,
            request_body=request_body if isinstance(request_body, dict) else {},
            response_body=response_body if isinstance(response_body, dict) else {},
            why_messages=[],
            tool_calls=[],
            client_ip=client_ip,
            user_agent=user_agent,
            session_id="",
            upstream=upstream_name,
            full_messages=[],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_kind=path_kind,
        )

        # Network-origin enrichment still applies — knowing which subnet
        # an embedding workload came from is just as forensically useful
        # as for chat.
        try:
            if network_origin.is_enabled():
                origin = network_origin.parse_from_request(request, upstream_url)
                ledger.record_request_network(entry.seq, entry.timestamp, origin)
        except Exception as _net_err:
            print(f"  ⚠ network-origin capture failed (non-chat): {_net_err}")
    except Exception as _err:
        print(f"  ⚠ non-chat ledger append failed: {_err}")


def _http_error_kind(status_code: int) -> str:
    """Bucket a non-200 HTTP status into a ledger ``request_kind``."""
    return "error_http_4xx" if 400 <= status_code < 500 else "error_http_5xx"


def _log_error_entry(
    request,
    request_body,
    upstream_name,
    upstream_url,
    path_kind,
    agent_id,
    error_kind,
    status_code,
    detail,
):
    """Append a ledger row for a failed upstream outcome — non-200 response,
    timeout, connection error, or interrupted stream.

    Reliability in the trust score counts ``action_type='error'`` rows (see
    ``trust._reliability_score``). Without these, every non-200 outcome
    skipped the ledger entirely and the success rate read optimistically. The
    failure class lives in ``request_kind`` (``error_http_4xx`` /
    ``error_http_5xx`` / ``error_timeout`` / ``error_upstream`` /
    ``error_stream``) while ``action_type`` stays a flat ``'error'`` so the
    reliability query needs no per-class enumeration.

    Best-effort, same contract as ``_log_entry``: any failure here is logged
    and swallowed — a row that can't be written must never turn one upstream
    failure into two.
    """
    try:
        client_ip = _client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:500]
        model = (
            request_body.get("model") if isinstance(request_body, dict) else None
        ) or ""
        if isinstance(detail, str):
            detail_text = detail[:500]
        else:
            try:
                detail_text = json.dumps(detail)[:500]
            except Exception:
                detail_text = str(detail)[:500]

        entry = ledger.append(
            agent_id=agent_id,
            action_type="error",
            model=model,
            request_body=request_body if isinstance(request_body, dict) else {},
            response_body={"error": detail_text, "status": status_code},
            why_messages=[],
            tool_calls=[],
            client_ip=client_ip,
            user_agent=user_agent,
            session_id="",
            upstream=upstream_name,
            full_messages=[],
            prompt_tokens=0,
            completion_tokens=0,
            request_kind=error_kind,
        )

        # Network-origin enrichment applies to failures too — which subnet a
        # timing-out workload came from is forensically useful.
        try:
            if network_origin.is_enabled():
                origin = network_origin.parse_from_request(request, upstream_url)
                ledger.record_request_network(entry.seq, entry.timestamp, origin)
        except Exception as _net_err:
            print(f"  ⚠ network-origin capture failed (error entry): {_net_err}")

        print(
            f"  ✓ Ledger [{entry.entry_id[:8]}] agent={agent_id} "
            f"action=error kind={error_kind} status={status_code}"
        )
    except Exception as _err:
        print(f"  ⚠ error ledger append failed: {_err}")


def _log_entry(
    request,
    request_body,
    response_body,
    messages,
    upstream_name="",
    upstream_url="",
):
    """Extract signals and append to ledger. Non-blocking best-effort."""
    try:
        tool_calls = _extract_tool_calls(response_body)
        why_context = _why_context(messages)
        full_ctx = _full_messages_context(messages)
        action = _action_type(request_body, response_body)
        kind = _request_kind(action, messages, response_body, tool_calls)
        agent_id = _agent_id(request)
        client_ip = _client_ip(request)
        user_agent = request.headers.get("User-Agent", "")[:500]
        session_id, request_turn_hashes = _session_id(request, messages)
        model = request_body.get("model", response_body.get("model", "unknown"))
        prompt_tokens, completion_tokens = _extract_token_usage(response_body)

        entry = ledger.append(
            agent_id=agent_id,
            action_type=action,
            model=model,
            request_body=request_body,
            response_body=response_body,
            why_messages=why_context,
            tool_calls=tool_calls,
            client_ip=client_ip,
            user_agent=user_agent,
            session_id=session_id,
            upstream=upstream_name,
            full_messages=full_ctx,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_kind=kind,
        )

        # Network-origin enrichment → request_network side table. Parsing is
        # pure Python, the INSERT is in the same pool — a few hundred µs on
        # the hot path. Gated so operators can disable it without a deploy.
        try:
            if network_origin.is_enabled():
                origin = network_origin.parse_from_request(request, upstream_url)
                ledger.record_request_network(entry.seq, entry.timestamp, origin)
        except Exception as _net_err:
            print(f"  ⚠ network-origin capture failed: {_net_err}")

        # Record turn fingerprints for thread reconstruction on the NEXT
        # request. Include the assistant's response text so the client's
        # follow-up (which carries our reply in its history) will hash-
        # match this session even if the earlier user turns get compacted.
        try:
            extra_hashes: list[str] = []
            import hashlib

            assistant_text = _extract_assistant_text(response_body)
            if (
                assistant_text
                and len(assistant_text.strip()) >= _SESSION_MIN_TURN_CHARS
            ):
                extra_hashes.append(
                    hashlib.sha256(
                        f"assistant\n{assistant_text.strip()}".encode(
                            "utf-8", errors="replace"
                        )
                    ).hexdigest()[:32]
                )
            all_hashes = list({*request_turn_hashes, *extra_hashes})
            if all_hashes:
                ledger.record_session_turns(session_id, all_hashes)
        except Exception as _turn_err:
            print(f"  ⚠ session-turn record failed: {_turn_err}")

        # Fire-and-forget DLP scan — does not add latency to the proxy response
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                _dlp.scan_and_store_entry(
                    entry_id=entry.entry_id,
                    session_id=entry.session_id,
                    seq=entry.seq,
                    messages=full_ctx,
                    response_body=response_body,
                )
            )
        except Exception as _dlp_err:
            print(f"  ⚠ DLP task dispatch failed: {_dlp_err}")

        print(
            f"  ✓ Ledger [{entry.entry_id[:8]}] "
            f"agent={agent_id} session={session_id} ip={client_ip} action={action} "
            f"tools={[tc['function'] for tc in tool_calls]}"
        )
    except Exception as e:
        # Never let ledger failures break the proxy
        print(f"  ⚠ Ledger write failed: {e}")
        # ITIL Phase 1: emit incident (safe to fail if dashboard not co-located)
        try:
            from .dashboard import _emit_incident

            _emit_incident("high", "ledger", f"Ledger write failed: {str(e)[:200]}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@_proxy_app.get("/health")
async def health():
    valid, errors = ledger.verify_chain()
    return {
        "status": "ok",
        "upstreams": sorted(UPSTREAMS.keys()),
        "ledger_valid": valid,
        "ledger_errors": errors,
    }
