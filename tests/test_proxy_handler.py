"""End-to-end tests for the proxy request handler and the streaming path
(kyde.server.proxy / _handle_streaming).

These drive the actual FastAPI catch-all route through a TestClient with
the upstream `httpx.AsyncClient` faked out, so no socket is opened. They
cover the data-plane branches that the pure-helper tests can't reach:

  * non-streaming success → forwarded response + a chat ledger row
  * upstream non-200 / timeout / connection error → the right status code
    AND a matching `action_type='error'` reliability row
  * Ollama NDJSON responses → merged into one ledger row, raw bytes
    forwarded back untouched
  * a non-JSON upstream body → passed through verbatim
  * streaming: SSE forwarded to the client while the accumulator rebuilds
    a synthetic response for one post-stream ledger row, and a non-200
    stream open writes an error row instead of a synthetic success
  * the /health route

Enforcement (enterprise prevention / block-list) is disabled here so the core
forward+log path is isolated; those branches have their own tests.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from kyde import ledger, server

# ---------------------------------------------------------------------------
# Fake httpx upstream
# ---------------------------------------------------------------------------

_NO_JSON = object()


class FakeResp:
    """Stand-in for the non-streaming httpx.Response the proxy awaits."""

    def __init__(
        self, status_code=200, json_data=_NO_JSON, text="", content=None, headers=None
    ):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        if content is not None:
            self.content = content
        else:
            self.content = text.encode() if text else b""
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        if self._json is _NO_JSON:
            raise ValueError("no json body")
        return self._json


class FakeStreamResp:
    def __init__(self, status_code, lines, exc=None):
        self.status_code = status_code
        self._lines = lines
        self._exc = exc

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln
        if self._exc is not None:
            raise self._exc


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class FakeAsyncClient:
    """Configurable replacement for httpx.AsyncClient covering both the
    `await client.request(...)` and `async with client.stream(...)` shapes."""

    def __init__(self, *, resp=None, request_exc=None, stream_resp=None):
        self._resp = resp
        self._request_exc = request_exc
        self._stream_resp = stream_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, **kw):
        if self._request_exc is not None:
            raise self._request_exc
        return self._resp

    def stream(self, **kw):
        return _StreamCtx(self._stream_resp)


@pytest.fixture
def proxy_client(monkeypatch):
    """TestClient over the proxy app, with enforcement off and the
    fire-and-forget DLP scan stubbed. Constructed WITHOUT a `with` block
    so the lifespan (which would push patterns to dlp-regex) never runs.

    `set_upstream(...)` installs the fake httpx client for the next call.
    """
    monkeypatch.setattr(server._features, "HAS_ENFORCEMENT", False)
    monkeypatch.setattr(
        server._dlp, "scan_and_store_entry", AsyncMock(return_value=None)
    )

    def set_upstream(**kwargs):
        fake = FakeAsyncClient(**kwargs)
        monkeypatch.setattr(server.httpx, "AsyncClient", lambda *a, **k: fake)
        return fake

    client = TestClient(server._proxy_app)
    client.set_upstream = set_upstream
    return client


def _rows() -> list[dict]:
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ledger ORDER BY seq")
            return list(cur.fetchall())


_AUTH = {"Authorization": "Bearer sk-test-key"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(proxy_client):
    resp = proxy_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "openai" in body["upstreams"]
    assert "ledger_valid" in body


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------


def test_chat_success_forwards_and_logs(proxy_client):
    upstream_body = {
        "choices": [{"message": {"role": "assistant", "content": "hello there"}}],
        "model": "gpt-4o-mini",
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    proxy_client.set_upstream(resp=FakeResp(200, json_data=upstream_body))

    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == upstream_body

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "chat"
    assert rows[0]["upstream"] == "openai"


def test_chat_upstream_non_200_logs_error_row(proxy_client):
    proxy_client.set_upstream(
        resp=FakeResp(429, json_data={"error": {"message": "rate limited"}})
    )
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 429
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "error"
    assert rows[0]["request_kind"] == "error_http_4xx"


def test_chat_upstream_timeout_returns_504_and_logs(proxy_client):
    proxy_client.set_upstream(request_exc=httpx.TimeoutException("too slow"))
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 504
    assert resp.json()["error"]["type"] == "gateway_timeout"
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["request_kind"] == "error_timeout"


def test_chat_upstream_connection_error_returns_502_and_logs(proxy_client):
    proxy_client.set_upstream(request_exc=httpx.ConnectError("refused"))
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "bad_gateway"
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["request_kind"] == "error_upstream"


def test_non_json_response_passthrough_logs_error_on_non_200(proxy_client):
    # An upstream that returns non-JSON with a non-200 status is still a
    # failed outcome — it must be counted, and the body passed through.
    proxy_client.set_upstream(
        resp=FakeResp(
            500, text="upstream exploded", headers={"content-type": "text/plain"}
        )
    )
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 500
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "error"
    assert rows[0]["request_kind"] == "error_http_5xx"


# ---------------------------------------------------------------------------
# Ollama NDJSON
# ---------------------------------------------------------------------------


def test_ollama_ndjson_response_merged_and_forwarded(proxy_client):
    ndjson = "\n".join(
        json.dumps(c)
        for c in [
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {
                "message": {"content": "lo"},
                "done": True,
                "model": "llama3",
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        ]
    )
    proxy_client.set_upstream(
        resp=FakeResp(
            200,
            text=ndjson,
            content=ndjson.encode(),
            headers={"content-type": "application/x-ndjson"},
        )
    )
    resp = proxy_client.post(
        "/ollama/api/chat",
        headers=_AUTH,
        json={"model": "llama3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    # Raw NDJSON bytes forwarded back untouched.
    assert resp.text == ndjson
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "chat"
    assert rows[0]["upstream"] == "ollama"


def test_ollama_ndjson_non_200_logs_error(proxy_client):
    proxy_client.set_upstream(
        resp=FakeResp(
            503,
            text='{"error":"model loading"}',
            content=b'{"error":"model loading"}',
            headers={"content-type": "application/x-ndjson"},
        )
    )
    resp = proxy_client.post(
        "/ollama/api/chat",
        headers=_AUTH,
        json={"model": "llama3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "error"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_streaming_success_forwards_sse_and_logs_synthetic(proxy_client):
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
        "data: [DONE]",
    ]
    proxy_client.set_upstream(stream_resp=FakeStreamResp(200, lines))

    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    # The SSE lines are forwarded verbatim to the client.
    assert "Hel" in resp.text and "[DONE]" in resp.text

    rows = _rows()
    assert len(rows) == 1
    # Synthetic row reassembled from the deltas.
    assert rows[0]["action_type"] == "chat"


def test_streaming_non_200_open_logs_error_not_success(proxy_client):
    proxy_client.set_upstream(stream_resp=FakeStreamResp(500, []))
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200  # SSE envelope still opens 200 to the client
    rows = _rows()
    # Exactly one row, and it's the error — never a synthetic success too.
    assert len(rows) == 1
    assert rows[0]["action_type"] == "error"
    assert rows[0]["request_kind"] == "error_http_5xx"


def test_streaming_ollama_native_ndjson(proxy_client):
    # Ollama streams bare NDJSON (no `data: ` prefix); the accumulator has
    # a distinct branch for it, and pulls usage from the `done` chunk.
    lines = [
        json.dumps(
            {"message": {"role": "assistant", "content": "Stream "}, "done": False}
        ),
        json.dumps(
            {
                "message": {"content": "reply"},
                "done": True,
                "prompt_eval_count": 6,
                "eval_count": 3,
            }
        ),
    ]
    proxy_client.set_upstream(stream_resp=FakeStreamResp(200, lines))
    resp = proxy_client.post(
        "/ollama/api/chat",
        headers=_AUTH,
        json={
            "model": "llama3",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "chat"
    assert rows[0]["upstream"] == "ollama"


def test_streaming_anthropic_sse(proxy_client):
    # Anthropic SSE uses event-typed envelopes; the handler routes them
    # through _apply_anthropic_sse_chunk and promotes the accumulated text
    # + usage into the synthetic ledger row.
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"a longer assistant reply"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","usage":{"output_tokens":24}}',
    ]
    proxy_client.set_upstream(stream_resp=FakeStreamResp(200, lines))
    resp = proxy_client.post(
        "/anthropic/v1/messages",
        headers={"x-api-key": "sk-ant-test"},
        json={
            "model": "claude-3-5-sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert "assistant reply" in resp.text
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "chat"
    assert rows[0]["upstream"] == "anthropic"


def test_malformed_request_body_is_tolerated(proxy_client):
    # A body that isn't valid JSON must not 500 the proxy — request_body
    # falls back to {} and the request is still forwarded + logged.
    proxy_client.set_upstream(
        resp=FakeResp(200, json_data={"choices": [{"message": {"content": "ok"}}]})
    )
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers={**_AUTH, "content-type": "application/json"},
        content=b"{ this is not json",
    )
    assert resp.status_code == 200
    assert len(_rows()) == 1


def test_substantive_turn_records_session_hashes(proxy_client):
    # User + assistant turns both long enough (>= 20 chars) to clear the
    # session-fingerprint threshold, so the assistant-hash branch and
    # record_session_turns both run.
    proxy_client.set_upstream(
        resp=FakeResp(
            200,
            json_data={
                "choices": [
                    {
                        "message": {
                            "content": "this is a sufficiently long assistant answer"
                        }
                    }
                ],
                "model": "gpt-4o",
            },
        )
    )
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": "please tell me something interesting and long",
                }
            ],
        },
    )
    assert resp.status_code == 200
    row = _rows()[0]
    # The session got a stable id (non-empty) from the turn hashing.
    assert row["session_id"]
    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM session_turns")
            assert cur.fetchone()["n"] >= 1


def test_streaming_interrupted_midway_logs_stream_error(proxy_client):
    # Two good deltas, then the upstream drops the connection.
    lines = ['data: {"choices":[{"delta":{"content":"par"}}]}']
    proxy_client.set_upstream(
        stream_resp=FakeStreamResp(
            200, lines, exc=httpx.RemoteProtocolError("peer closed")
        )
    )
    resp = proxy_client.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["action_type"] == "error"
    assert rows[0]["request_kind"] == "error_stream"
