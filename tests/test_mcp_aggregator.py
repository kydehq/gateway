"""
Tests for kyde.mcp_aggregator — the bare-`/mcp` endpoint that serves
a namespaced union of every backend's tools and routes `tools/call` to
the right per-server proxy.

Two layers exercised here:
  1. Pure catalog functions (seed_from_tools_list / catalog_snapshot /
     namespace round-trip) — no FastAPI, no DB beyond what mcp_registry
     needs to validate referenced server names.
  2. End-to-end via the proxy app's TestClient: tools/list returns the
     catalog, tools/call rewrites the namespaced name and delegates to
     mcp_proxy.handle_mcp_request so the M2 ledger row carries the
     un-namespaced tool name.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from kyde import (
    dlp_json_walk,
    mcp_aggregator,
    mcp_ledger,
    mcp_policy,
    mcp_proxy,
    mcp_registry,
    server,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_client():
    return TestClient(server.proxy_app)


@pytest.fixture(autouse=True)
def _reset_caches():
    mcp_registry.invalidate_cache()
    mcp_policy.invalidate_cache()
    mcp_aggregator.invalidate_cache()
    yield
    mcp_registry.invalidate_cache()
    mcp_policy.invalidate_cache()
    mcp_aggregator.invalidate_cache()


@pytest.fixture(autouse=True)
def _stub_dlp(monkeypatch):
    """No-op DLP — covered in its own test file. Keeps these tests fast."""

    async def _empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(dlp_json_walk, "scan_request", _empty)
    monkeypatch.setattr(dlp_json_walk, "scan_response", _empty)


@pytest.fixture(autouse=True)
def _stub_ledger(monkeypatch):
    """Stub the ledger writer — aggregator tests don't care about chain
    integrity, only that the right call is made with the right backend."""

    async def _noop(**kwargs):
        kwargs.setdefault("_stub", True)

        class _Entry:
            entry_id = "stub"

        return _Entry()

    monkeypatch.setattr(mcp_ledger, "record_mcp_call", _noop)


class _FakeAsyncClient:
    """Records the outbound call and returns a canned httpx.Response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes = b'{"jsonrpc":"2.0","id":1,"result":{}}',
        headers: Optional[dict[str, str]] = None,
    ):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {"content-type": "application/json"}
        self.captured: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def request(self, *, method, url, headers, content):
        self.captured = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "content": content,
        }
        return httpx.Response(
            status_code=self.status_code, content=self.body, headers=self.headers
        )


def _install_fake(monkeypatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr(mcp_proxy.httpx, "AsyncClient", lambda *_a, **_k: fake)


# ---------------------------------------------------------------------------
# Catalog primitives
# ---------------------------------------------------------------------------


def test_seed_namespaces_tools_by_server():
    mcp_aggregator.seed_from_tools_list(
        "github",
        [{"name": "search_repositories"}, {"name": "create_issue"}],
    )
    snap = mcp_aggregator.catalog_snapshot()
    names = {item["tool"]["name"] for item in snap["items"]}
    assert names == {"github__search_repositories", "github__create_issue"}
    assert snap["server_count"] == 1
    assert snap["tool_count"] == 2


def test_second_seed_replaces_prior_snapshot_for_that_server():
    """Empty list ⇒ that server's slice is cleared. Otherwise a tool the
    upstream just removed would linger in the catalog until TTL expiry."""
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "a"}, {"name": "b"}])
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "a"}])
    snap = mcp_aggregator.catalog_snapshot()
    names = [item["tool"]["name"] for item in snap["items"]]
    assert names == ["svc__a"]


def test_seed_preserves_other_servers():
    mcp_aggregator.seed_from_tools_list("svc1", [{"name": "x"}])
    mcp_aggregator.seed_from_tools_list("svc2", [{"name": "y"}])
    mcp_aggregator.seed_from_tools_list("svc1", [{"name": "x2"}])
    names = {
        item["tool"]["name"] for item in mcp_aggregator.catalog_snapshot()["items"]
    }
    assert names == {"svc1__x2", "svc2__y"}


def test_seed_skips_malformed_tools():
    mcp_aggregator.seed_from_tools_list(
        "svc",
        [
            {"name": "ok"},
            {"description": "no name"},
            "not a dict",  # type: ignore[list-item]
            {"name": ""},
            {"name": 42},
        ],
    )
    names = {
        item["tool"]["name"] for item in mcp_aggregator.catalog_snapshot()["items"]
    }
    assert names == {"svc__ok"}


def test_snapshot_evicts_after_ttl(monkeypatch):
    """Entries older than _CATALOG_TTL_S disappear from the snapshot.

    We monkeypatch time.monotonic instead of sleeping for 5 minutes —
    same observable behaviour, no flaky timing."""
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "tool"}])
    # Fake-forward time past the TTL.
    real = mcp_aggregator.time.monotonic
    base = real()
    monkeypatch.setattr(
        mcp_aggregator.time,
        "monotonic",
        lambda: base + mcp_aggregator._CATALOG_TTL_S + 1,
    )
    snap = mcp_aggregator.catalog_snapshot()
    assert snap["items"] == []
    assert snap["tool_count"] == 0


def test_split_ns_round_trip():
    assert mcp_aggregator._split_ns("github__create_issue") == (
        "github",
        "create_issue",
    )
    # Tool names may themselves contain '__'; partition keeps everything
    # after the first delimiter as the tool half.
    assert mcp_aggregator._split_ns("svc__deep__namespace__name") == (
        "svc",
        "deep__namespace__name",
    )


def test_split_ns_rejects_missing_delimiter():
    assert mcp_aggregator._split_ns("just_a_tool") is None
    assert mcp_aggregator._split_ns("__leading") is None
    assert mcp_aggregator._split_ns("trailing__") is None


# ---------------------------------------------------------------------------
# End-to-end: bare /mcp through the proxy_app
# ---------------------------------------------------------------------------


def test_aggregator_tools_list_returns_union_of_catalog(proxy_client):
    mcp_aggregator.seed_from_tools_list("svc1", [{"name": "alpha"}])
    mcp_aggregator.seed_from_tools_list("svc2", [{"name": "beta"}])

    resp = proxy_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    body = resp.json()
    assert resp.status_code == 200
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == {"svc1__alpha", "svc2__beta"}
    assert body["id"] == 1


def test_aggregator_tools_list_with_trailing_slash(proxy_client):
    mcp_aggregator.seed_from_tools_list("svc1", [{"name": "alpha"}])
    resp = proxy_client.post(
        "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {"svc1__alpha"}


def test_aggregator_tools_list_empty_when_no_servers_seeded(proxy_client):
    resp = proxy_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    body = resp.json()
    assert body["result"]["tools"] == []


def test_aggregator_tools_call_routes_to_backend_with_stripped_prefix(
    proxy_client, monkeypatch
):
    """The whole point of M4: a tools/call to 'svc__search' rewrites the
    body to params.name='search' and delegates to the per-server proxy,
    which forwards to that server's upstream."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "search"}])

    fake = _FakeAsyncClient(body=b'{"jsonrpc":"2.0","id":1,"result":{}}')
    _install_fake(monkeypatch, fake)

    envelope = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": "svc__search", "arguments": {"q": "kyde"}},
    }
    resp = proxy_client.post("/mcp", json=envelope)
    assert resp.status_code == 200

    # The downstream forward must use the un-namespaced tool name.
    forwarded = json.loads(fake.captured["content"])
    assert forwarded["params"]["name"] == "search"
    assert forwarded["params"]["arguments"] == {"q": "kyde"}
    # Routed to the right upstream URL.
    assert fake.captured["url"] == "https://upstream.test/mcp"


def test_aggregator_tools_call_forwards_agent_authorization(proxy_client, monkeypatch):
    """Each tools/call routes to exactly one backend; agent auth still
    passes through unchanged — the transparency invariant must hold for
    the aggregator path too."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "ping"}])

    fake = _FakeAsyncClient(body=b'{"jsonrpc":"2.0","id":1,"result":{}}')
    _install_fake(monkeypatch, fake)

    resp = proxy_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "svc__ping"},
        },
        headers={"Authorization": "Bearer agent-secret-aggregator"},
    )
    assert resp.status_code == 200
    assert (
        fake.captured["headers"].get("authorization")
        == "Bearer agent-secret-aggregator"
    )


def test_aggregator_tools_call_missing_namespace_returns_jsonrpc_error(proxy_client):
    """A bare tool name (no '__') is an error — the catalog deals in
    namespaced names exclusively."""
    resp = proxy_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search"},
        },
    )
    body = resp.json()
    assert body["error"]["code"] == -32602
    assert "namespaced" in body["error"]["message"]


def test_aggregator_tools_call_unknown_server_returns_minus_32000(proxy_client):
    resp = proxy_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ghost__search"},
        },
    )
    body = resp.json()
    assert body["error"]["code"] == -32000
    assert "ghost" in body["error"]["message"]


def test_aggregator_rejects_unsupported_methods(proxy_client):
    """v1 scope: only tools/list and tools/call. resources/* etc return
    method-not-found rather than silently delegating."""
    resp = proxy_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
    )
    body = resp.json()
    assert body["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Catalog seeding from real per-server traffic
# ---------------------------------------------------------------------------


def test_per_server_tools_list_seeds_aggregator_catalog(proxy_client, monkeypatch):
    """A tools/list passing through /mcp/svc should populate the aggregator
    catalog without an explicit probe."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    fake = _FakeAsyncClient(
        body=b'{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"a"},{"name":"b"}]}}'
    )
    _install_fake(monkeypatch, fake)

    proxy_client.post(
        "/mcp/svc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    snap = mcp_aggregator.catalog_snapshot()
    names = {item["tool"]["name"] for item in snap["items"]}
    assert names == {"svc__a", "svc__b"}


def test_failed_tools_list_does_not_clobber_catalog(proxy_client, monkeypatch):
    """A 5xx upstream must not wipe a previously good snapshot — that would
    let one bad call empty the aggregator until the next probe."""
    mcp_registry.upsert_server("svc", "https://upstream.test/mcp")
    mcp_aggregator.seed_from_tools_list("svc", [{"name": "cached"}])

    fake = _FakeAsyncClient(status_code=500, body=b'{"error":"boom"}')
    _install_fake(monkeypatch, fake)

    proxy_client.post(
        "/mcp/svc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {
        item["tool"]["name"] for item in mcp_aggregator.catalog_snapshot()["items"]
    }
    assert names == {"svc__cached"}
