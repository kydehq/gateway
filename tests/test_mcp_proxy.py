"""
Tests for kyde.mcp_proxy (M0) — JSON-RPC envelope parse, registry
lookup, and Authorization pass-through.

These run against the FastAPI TestClient on `server.proxy_app` so the
real /mcp/{server_name} route is exercised. httpx.AsyncClient is faked
out so no network traffic leaves the process; the fake records exactly
what would have been sent upstream, which is what the M0 contract is
really about (the byte-for-byte forward).
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from kyde import (
    dlp_json_walk,
    mcp_ledger,
    mcp_policy,
    mcp_proxy,
    mcp_registry,
    server,
)


# ---------------------------------------------------------------------------
# Fake upstream — replaces httpx.AsyncClient inside mcp_proxy.
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """Record the outbound call and return a canned httpx.Response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes = b'{"jsonrpc":"2.0","id":1,"result":{}}',
        headers: Optional[dict[str, str]] = None,
        raise_exc: Optional[Exception] = None,
    ):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {"content-type": "application/json"}
        self.raise_exc = raise_exc
        # Filled in when request() runs so tests can assert on it.
        self.captured: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def request(self, *, method, url, headers, content):
        self.captured = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "content": content,
        }
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(
            status_code=self.status_code,
            content=self.body,
            headers=self.headers,
        )


def _install_fake_client(monkeypatch, fake: _FakeAsyncClient) -> None:
    """Patch httpx.AsyncClient inside mcp_proxy to a factory returning `fake`."""

    def _factory(*_args, **_kwargs):
        return fake

    monkeypatch.setattr(mcp_proxy.httpx, "AsyncClient", _factory)


@pytest.fixture
def proxy_client():
    return TestClient(server.proxy_app)


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    mcp_registry.invalidate_cache()
    mcp_policy.invalidate_cache()
    yield
    mcp_registry.invalidate_cache()
    mcp_policy.invalidate_cache()


@pytest.fixture(autouse=True)
def _stub_dlp(monkeypatch):
    """Replace the DLP walker entry points with no-op stubs so the proxy
    tests don't need the regex/BERT sidecars running. Findings-specific
    behaviour is covered in tests/test_dlp_json_walk.py."""

    async def _empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(dlp_json_walk, "scan_request", _empty)
    monkeypatch.setattr(dlp_json_walk, "scan_response", _empty)


# ---------------------------------------------------------------------------
# Envelope parsing — JSON-RPC -32700 surface
# ---------------------------------------------------------------------------


def test_empty_body_returns_parse_error(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    # No fake client needed — we shouldn't reach the forward path.
    resp = proxy_client.post("/mcp/svc", content=b"")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32700
    assert "empty" in body["error"]["message"].lower()


def test_malformed_json_returns_parse_error(proxy_client):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    resp = proxy_client.post("/mcp/svc", content=b"{not json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32700


def test_non_object_envelope_returns_parse_error(proxy_client):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    resp = proxy_client.post("/mcp/svc", content=b"[1,2,3]")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Registry resolution — JSON-RPC -32000 surface
# ---------------------------------------------------------------------------


def test_unknown_server_returns_minus_32000_with_request_id(proxy_client):
    envelope = {"jsonrpc": "2.0", "id": 42, "method": "tools/list"}
    resp = proxy_client.post("/mcp/no-such-server", json=envelope)
    body = resp.json()
    assert body["error"]["code"] == -32000
    assert "not registered" in body["error"]["message"]
    # The request id must be echoed so the client can correlate.
    assert body["id"] == 42


def test_disabled_server_returns_minus_32000(proxy_client):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp", enabled=False)
    envelope = {"jsonrpc": "2.0", "id": "abc", "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    body = resp.json()
    assert body["error"]["code"] == -32000
    assert "disabled" in body["error"]["message"]
    assert body["id"] == "abc"


# ---------------------------------------------------------------------------
# Forwarding contract — Authorization pass-through and body fidelity.
# ---------------------------------------------------------------------------


def test_authorization_header_passes_through_unchanged(proxy_client, monkeypatch):
    """The whole point of M0: the gateway is transparent on upstream auth."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient()
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    bearer = "Bearer agent-secret-xyz"
    resp = proxy_client.post(
        "/mcp/svc",
        json=envelope,
        headers={"Authorization": bearer},
    )
    assert resp.status_code == 200
    # Lowercase per Starlette's header normalization.
    assert fake.captured["headers"].get("authorization") == bearer


def test_internal_attribution_header_is_stripped(proxy_client, monkeypatch):
    """x-agent-id is internal-only; upstream MCP servers must never see it."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient()
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    proxy_client.post(
        "/mcp/svc",
        json=envelope,
        headers={"X-Agent-ID": "agent:leaky"},
    )
    forwarded = {k.lower() for k in fake.captured["headers"]}
    assert "x-agent-id" not in forwarded


def test_hop_by_hop_headers_are_stripped(proxy_client, monkeypatch):
    """host/content-length are framing concerns; httpx will set them itself."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient()
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    proxy_client.post("/mcp/svc", json=envelope)
    forwarded = {k.lower() for k in fake.captured["headers"]}
    assert "host" not in forwarded
    assert "content-length" not in forwarded


def test_request_body_is_forwarded_byte_for_byte(proxy_client, monkeypatch):
    """The envelope bytes the agent sent must reach the upstream unchanged.

    This is load-bearing for later milestones: DLP scans and the ledger
    entry hash both need the canonical request bytes to be the bytes the
    upstream actually saw.
    """
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient()
    _install_fake_client(monkeypatch, fake)

    # Pick a body with whitespace patterns that re-serialization would
    # destroy, so a silent json.dumps/json.loads round-trip would fail
    # this test.
    raw = b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"x":1}}'
    resp = proxy_client.post(
        "/mcp/svc",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert fake.captured["content"] == raw


def test_forwards_to_configured_upstream_url(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/foo/mcp")
    fake = _FakeAsyncClient()
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    proxy_client.post("/mcp/svc", json=envelope)
    assert fake.captured["url"] == "https://upstream.test/foo/mcp"
    assert fake.captured["method"] == "POST"


def test_upstream_response_body_returned_verbatim(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    upstream_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": ["a", "b"]}}
    ).encode()
    fake = _FakeAsyncClient(status_code=200, body=upstream_body)
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200
    assert resp.content == upstream_body


def test_upstream_status_code_propagates(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(status_code=502, body=b"upstream said no")
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 502


def test_response_content_length_is_not_double_declared(proxy_client, monkeypatch):
    """httpx already resolved the content length; we strip it so Starlette
    recomputes from the body it's actually sending."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(
        body=b'{"jsonrpc":"2.0","id":1,"result":{}}',
        headers={"content-type": "application/json", "content-length": "9999"},
    )
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    # The stale 9999 must NOT survive — Starlette/httpx should recompute.
    cl = resp.headers.get("content-length")
    assert cl != "9999"


# ---------------------------------------------------------------------------
# Upstream transport failures — JSON-RPC -32002 surface
# ---------------------------------------------------------------------------


def test_upstream_connect_error_returns_minus_32002(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(raise_exc=httpx.ConnectError("DNS go boom"))
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 99, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    body = resp.json()
    assert body["error"]["code"] == -32002
    assert "ConnectError" in body["error"]["message"]
    assert body["id"] == 99


def test_upstream_timeout_returns_minus_32002(proxy_client, monkeypatch):
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(raise_exc=httpx.ReadTimeout("slow upstream"))
    _install_fake_client(monkeypatch, fake)

    envelope = {"jsonrpc": "2.0", "id": 99, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    body = resp.json()
    assert body["error"]["code"] == -32002


# ---------------------------------------------------------------------------
# M2 — Ledger + DLP sandwich
# ---------------------------------------------------------------------------


def _spy_record(captured: list[dict]):
    """Drop-in replacement for mcp_ledger.record_mcp_call that records the
    kwargs it was called with."""

    async def _spy(**kwargs):
        captured.append(kwargs)

        # Return a stub LedgerEntry-shaped object — the proxy doesn't read it.
        class _Stub:
            entry_id = "stub"

        return _Stub()

    return _spy


def test_successful_call_invokes_record_with_outcome_ok(proxy_client, monkeypatch):
    """Every forwarded call must produce exactly one ledger row via
    mcp_ledger.record_mcp_call, with outcome='ok'."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(
        body=b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}',
    )
    _install_fake_client(monkeypatch, fake)

    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200
    assert len(captured) == 1
    call = captured[0]
    assert call["outcome"] == "ok"
    assert call["envelope"]["method"] == "tools/list"
    assert call["backend"]["name"] == "svc"
    assert call["dlp_findings"] == []
    assert call["duration_ms"] >= 0


def test_upstream_error_still_records_call(proxy_client, monkeypatch):
    """Transport failures must still land in the ledger — otherwise the
    dashboard can't surface flaky backends."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(raise_exc=httpx.ConnectError("DNS"))
    _install_fake_client(monkeypatch, fake)

    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200  # JSON-RPC error envelope, not HTTP error
    assert resp.json()["error"]["code"] == -32002

    assert len(captured) == 1
    assert captured[0]["outcome"] == "upstream_error"
    assert captured[0]["upstream_response"] is None
    assert captured[0]["upstream_body"] is None


def test_registry_failures_skip_ledger(proxy_client, monkeypatch):
    """Unknown/disabled server is rejected before any forward attempt;
    no ledger row should be written for a request that never reached an
    upstream."""
    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    resp = proxy_client.post(
        "/mcp/no-such",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32000
    assert captured == []


def test_dlp_findings_flow_through_to_record(proxy_client, monkeypatch):
    """DLP findings collected from the request + response walkers must be
    handed to record_mcp_call so they end up in dlp_alerts."""
    from kyde import dlp

    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(
        body=b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"r"}]}}'
    )
    _install_fake_client(monkeypatch, fake)

    req_finding = dlp.DlpFinding(
        scanner="regex", alert=True, score=0.9, findings=[{"matched_value": "req"}]
    )
    resp_finding = dlp.DlpFinding(
        scanner="regex", alert=True, score=0.9, findings=[{"matched_value": "resp"}]
    )

    async def fake_scan_request(*_a, **_k):
        return [req_finding]

    async def fake_scan_response(*_a, **_k):
        return [resp_finding]

    monkeypatch.setattr(dlp_json_walk, "scan_request", fake_scan_request)
    monkeypatch.setattr(dlp_json_walk, "scan_response", fake_scan_response)

    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"q": "req"}},
    }
    proxy_client.post("/mcp/svc", json=envelope)

    assert len(captured) == 1
    findings = captured[0]["dlp_findings"]
    # request + response findings are concatenated.
    assert len(findings) == 2
    matched = {f.findings[0]["matched_value"] for f in findings}
    assert matched == {"req", "resp"}


# ---------------------------------------------------------------------------
# M3 — Per-tool policy gate
# ---------------------------------------------------------------------------


def test_denied_tools_call_returns_minus_32001_and_records_blocked(
    proxy_client, monkeypatch
):
    """A deny policy short-circuits the forward, returns -32001, and still
    leaves a signed `mcp_blocked` ledger row so auditors see the attempt."""
    backend = mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_policy.upsert_policy(
        str(backend["id"]), "*", "dangerous", "deny", "pii-leak risk", None
    )

    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    # The fake client would explode if the proxy tried to forward — leaving
    # it un-patched ensures any accidental forward shows up as a connect
    # error rather than a silent test pass.

    envelope = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "dangerous", "arguments": {}},
    }
    resp = proxy_client.post("/mcp/svc", json=envelope)
    body = resp.json()
    assert body["error"]["code"] == -32001
    assert "pii-leak risk" in body["error"]["message"]
    assert body["id"] == 5

    # One ledger row, outcome=blocked, no upstream artefacts.
    assert len(captured) == 1
    assert captured[0]["outcome"] == "blocked"
    assert captured[0]["upstream_response"] is None
    assert captured[0]["upstream_body"] is None


def test_allowed_tools_call_forwards_normally(proxy_client, monkeypatch):
    """An allow policy is the same as no policy — the call must reach the
    upstream and produce an outcome='ok' ledger row."""
    backend = mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_policy.upsert_policy(
        str(backend["id"]), "*", "search", "allow", "explicitly allowed", None
    )

    fake = _FakeAsyncClient(body=b'{"jsonrpc":"2.0","id":1,"result":{}}')
    _install_fake_client(monkeypatch, fake)
    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"q": "kyde"}},
    }
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200
    assert fake.captured["url"] == "https://upstream.test/mcp"
    assert len(captured) == 1
    assert captured[0]["outcome"] == "ok"


def test_policy_only_applies_to_tools_call(proxy_client, monkeypatch):
    """resources/read and metadata methods (tools/list, initialize, …)
    bypass the gate in v1 — the plan scopes M3 to tools/call only."""
    backend = mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    # A blanket deny would block everything if the gate fired on every method.
    mcp_policy.upsert_policy(str(backend["id"]), "*", "*", "deny", "lockdown", None)

    fake = _FakeAsyncClient(body=b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')
    _install_fake_client(monkeypatch, fake)
    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200
    # tools/list reached the upstream — the deny didn't fire.
    assert fake.captured.get("url") == "https://upstream.test/mcp"
    assert captured[0]["outcome"] == "ok"


def test_per_agent_deny_does_not_block_other_agents(proxy_client, monkeypatch):
    """The most-specific-wins ladder means a per-agent deny is scoped to
    that agent. Other agents fall through to default-allow."""
    backend = mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_policy.upsert_policy(
        str(backend["id"]), "agent:blocked", "search", "deny", "scoped", None
    )

    fake = _FakeAsyncClient(body=b'{"jsonrpc":"2.0","id":1,"result":{}}')
    _install_fake_client(monkeypatch, fake)
    captured: list[dict] = []
    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _spy_record(captured))

    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search"},
    }
    # Without an x-agent-id header, the proxy falls back to "agent:anonymous"
    # via the chat-path helper — it will *not* match "agent:blocked".
    resp = proxy_client.post("/mcp/svc", json=envelope)
    assert resp.status_code == 200
    assert resp.json().get("error") is None
    assert captured[0]["outcome"] == "ok"
