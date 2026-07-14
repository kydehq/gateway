"""Pure-function tests for the proxy's routing / normalization helpers
(kyde.server).

These are the request-shaping helpers the data plane runs on every call,
none of which previously had direct coverage:

  * `_resolve_upstream` — path → (provider, config, forwarded-path), the
    auto-detection that needs no per-agent config.
  * `_build_upstream_url` — joining base + api_prefix + path.
  * `_decompress_body` — gzip/deflate/… inflation for parsing (fail-open).
  * `_should_log_path` — the chat-shaped allow-list.
  * `_normalize_request_messages` / `_merge_ndjson_chunks` /
    `_normalize_response` — Ollama-native → OpenAI-shape coercion so the
    ledger schema stays uniform.

All pure: no HTTP, no DB.
"""

from __future__ import annotations

import gzip
import json
import zlib

import pytest

from kyde import server


# ---------------------------------------------------------------------------
# _resolve_upstream
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,exp_name,exp_api",
    [
        # Unprefixed → OpenAI default; v1/ stripped for detection.
        ("v1/chat/completions", "openai", "chat/completions"),
        ("chat/completions", "openai", "chat/completions"),
        # Explicit provider prefix; OpenAI/Anthropic re-add /v1 via api_prefix
        # so the client's v1/ is stripped to avoid doubling.
        ("openai/v1/chat/completions", "openai", "chat/completions"),
        ("anthropic/v1/messages", "anthropic", "messages"),
        # `claude` is an alias that resolves to anthropic.
        ("claude/v1/messages", "anthropic", "messages"),
        # Endpoint auto-detection: /v1/messages and bare messages → Anthropic.
        ("v1/messages", "anthropic", "messages"),
        ("messages", "anthropic", "messages"),
        # Empty-api_prefix upstreams (Ollama) forward the path verbatim —
        # no v1/ stripping.
        ("ollama/api/chat", "ollama", "api/chat"),
        ("ollama/v1/chat/completions", "ollama", "v1/chat/completions"),
        # v1 placed before the provider name.
        ("v1/openai/chat/completions", "openai", "chat/completions"),
        # Unknown path → OpenAI fallback, forwarded unchanged.
        ("foo/bar", "openai", "foo/bar"),
    ],
)
def test_resolve_upstream(path, exp_name, exp_api):
    name, _config, api_path = server._resolve_upstream(path)
    assert name == exp_name
    assert api_path == exp_api


def test_resolve_upstream_leading_slash_is_tolerated():
    name, _c, api = server._resolve_upstream("/anthropic/v1/messages")
    assert name == "anthropic"
    assert api == "messages"


# ---------------------------------------------------------------------------
# _build_upstream_url
# ---------------------------------------------------------------------------


def test_build_url_with_api_prefix():
    cfg = {"base": "https://api.openai.com", "api_prefix": "/v1"}
    assert (
        server._build_upstream_url(cfg, "chat/completions")
        == "https://api.openai.com/v1/chat/completions"
    )


def test_build_url_without_api_prefix():
    cfg = {"base": "http://ollama:11434", "api_prefix": ""}
    assert server._build_upstream_url(cfg, "api/chat") == "http://ollama:11434/api/chat"


def test_build_url_strips_redundant_slashes():
    cfg = {"base": "https://x.test/", "api_prefix": "/v1/"}
    assert server._build_upstream_url(cfg, "/messages") == "https://x.test/v1/messages"


# ---------------------------------------------------------------------------
# _decompress_body
# ---------------------------------------------------------------------------


def test_decompress_gzip():
    raw = b'{"hello":"world"}'
    assert server._decompress_body(gzip.compress(raw), "gzip") == raw


def test_decompress_deflate():
    raw = b'{"a":1}'
    assert server._decompress_body(zlib.compress(raw), "deflate") == raw


def test_decompress_uppercase_and_whitespace_encoding():
    raw = b"payload"
    assert server._decompress_body(gzip.compress(raw), "  GZIP ") == raw


def test_decompress_no_encoding_passthrough():
    assert server._decompress_body(b"plain", "") == b"plain"


def test_decompress_empty_body_passthrough():
    assert server._decompress_body(b"", "gzip") == b""


def test_decompress_unknown_encoding_passthrough():
    assert server._decompress_body(b"asis", "identity") == b"asis"


def test_decompress_corrupt_data_fails_open():
    # Not valid gzip — must return the original bytes rather than raise.
    assert server._decompress_body(b"not-gzip", "gzip") == b"not-gzip"


@pytest.mark.parametrize("enc", ["br", "zstd", "zstandard"])
def test_decompress_optional_codec_fails_open_when_lib_absent(enc):
    # brotli / zstandard are optional deps. When they aren't installed the
    # import raises inside the try and the helper must fail open (forward
    # the original bytes) rather than 500 the proxy. If the lib *is* present
    # this still holds for non-codec bytes, which won't decode.
    assert (
        server._decompress_body(b"raw-bytes-not-compressed", enc)
        == b"raw-bytes-not-compressed"
    )


# ---------------------------------------------------------------------------
# _should_log_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_path,expected",
    [
        ("chat/completions", True),
        ("v1/chat/completions", True),  # v1/ normalised away
        ("messages", True),
        ("api/chat", True),
        ("api/generate", True),
        ("embeddings", False),
        ("v1/embeddings", False),
        ("models", False),
        ("", False),
    ],
)
def test_should_log_path(api_path, expected):
    assert server._should_log_path(api_path) is expected


# ---------------------------------------------------------------------------
# _normalize_request_messages
# ---------------------------------------------------------------------------


def test_normalize_request_messages_default_returns_messages():
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert (
        server._normalize_request_messages("chat/completions", body) == body["messages"]
    )


def test_normalize_request_messages_missing_messages_is_empty():
    assert server._normalize_request_messages("chat/completions", {}) == []


def test_normalize_request_messages_ollama_generate_wraps_prompt():
    out = server._normalize_request_messages("api/generate", {"prompt": "write a poem"})
    assert out == [{"role": "user", "content": "write a poem"}]


def test_normalize_request_messages_ollama_generate_empty_prompt():
    assert server._normalize_request_messages("api/generate", {"prompt": ""}) == []


# ---------------------------------------------------------------------------
# _merge_ndjson_chunks
# ---------------------------------------------------------------------------


def test_merge_ndjson_api_chat_accumulates_content():
    body = "\n".join(
        json.dumps(c)
        for c in [
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": True, "eval_count": 5},
        ]
    )
    merged = server._merge_ndjson_chunks("api/chat", body)
    assert merged["message"]["content"] == "Hello"
    assert merged["message"]["role"] == "assistant"
    assert merged["done"] is True  # carries last chunk's fields


def test_merge_ndjson_api_generate_accumulates_response():
    body = "\n".join(
        json.dumps(c)
        for c in [
            {"response": "foo ", "done": False},
            {"response": "bar", "done": True},
        ]
    )
    merged = server._merge_ndjson_chunks("api/generate", body)
    assert merged["response"] == "foo bar"


def test_merge_ndjson_empty_body_returns_none():
    assert server._merge_ndjson_chunks("api/chat", "   \n  ") is None


def test_merge_ndjson_bad_json_returns_none():
    assert server._merge_ndjson_chunks("api/chat", "{not json}") is None


def test_merge_ndjson_non_ollama_path_returns_none():
    body = json.dumps({"message": {"content": "x"}})
    assert server._merge_ndjson_chunks("chat/completions", body) is None


# ---------------------------------------------------------------------------
# _normalize_response
# ---------------------------------------------------------------------------


def test_normalize_response_api_chat_to_openai_shape():
    ollama = {
        "message": {"role": "assistant", "content": "hi", "tool_calls": []},
        "model": "llama3",
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 7,
    }
    out = server._normalize_response("api/chat", ollama)
    choice = out["choices"][0]
    assert choice["message"]["content"] == "hi"
    assert choice["finish_reason"] == "stop"
    assert out["model"] == "llama3"
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 7}


def test_normalize_response_api_generate_to_openai_shape():
    ollama = {"response": "the answer", "model": "llama3", "eval_count": 3}
    out = server._normalize_response("api/generate", ollama)
    assert out["choices"][0]["message"]["content"] == "the answer"
    # done_reason absent → defaults to "stop".
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["completion_tokens"] == 3


def test_normalize_response_openai_path_passes_through():
    body = {"choices": [{"message": {"content": "already openai"}}]}
    assert server._normalize_response("chat/completions", body) is body


def test_normalize_response_non_dict_passes_through():
    assert server._normalize_response("api/chat", "not-a-dict") == "not-a-dict"
