"""
HTTP tests for the Agent Topology API (/api/topology, /api/topology/segment/...).

Seeds a small synthetic traffic matrix across ledger + request_network and
hits both endpoints through the FastAPI app so the global auth middleware
is exercised.
"""

from __future__ import annotations

from typing import Any

from kyde import auth, ledger

PASSWORD = "CorrectHorse!Battery9"


def _seed_admin_and_login(client) -> None:
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.post(
        "/login",
        data={"username": "admin", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _append(**overrides: Any) -> ledger.LedgerEntry:
    defaults = dict(
        agent_id="agent:a",
        action_type="chat",
        model="gpt-4o",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[],
        tool_calls=[],
        upstream="openai",
        session_id="s-default",
    )
    defaults.update(overrides)
    return ledger.append(**defaults)


def _attach_network(
    seq: int,
    timestamp: float,
    *,
    subnet: str,
    klass: str,
    tool: str,
    upstream_host: str,
    remote: str = "10.0.0.1",
) -> None:
    origin = type(
        "O",
        (),
        {
            "remote_addr": remote,
            "forwarded_chain": [],
            "forwarded_for_raw": "",
            "forwarded_raw": "",
            "via_raw": "",
            "origin_ip": None,
            "origin_class": klass,
            "origin_subnet": subnet,
            "ua_tool": tool,
            "ua_version": "1.0.0",
            "ua_os": "",
            "upstream_host": upstream_host,
            "upstream_region": "",
        },
    )()
    ledger.record_request_network(seq, timestamp, origin)


def _seed_topology_traffic() -> None:
    """Two segments × two tools × two upstream hosts × two models."""
    specs = [
        # segment         class      tool      upstream_host        model       n
        ("10.4.0.0/24", "rfc1918", "cursor", "api.openai.com", "gpt-4o", 3),
        ("10.4.0.0/24", "rfc1918", "cursor", "api.anthropic.com", "claude-4", 2),
        ("10.4.0.0/24", "rfc1918", "copilot", "api.openai.com", "gpt-4o", 4),
        ("203.0.113.0/24", "public", "cursor", "api.openai.com", "gpt-4o", 1),
        ("203.0.113.0/24", "public", "claude-code", "api.anthropic.com", "claude-4", 5),
    ]
    for seg, klass, tool, up_host, model, n in specs:
        for _ in range(n):
            e = _append(
                model=model,
                upstream="openai" if "openai" in up_host else "anthropic",
                agent_id=f"agent:{seg}:{tool}",
                session_id=f"s-{seg}-{tool}",
            )
            _attach_network(
                e.seq,
                e.timestamp,
                subnet=seg,
                klass=klass,
                tool=tool,
                upstream_host=up_host,
            )


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_topology_requires_auth(client):
    # Middleware wraps /api/* and returns 401 for unauthenticated callers.
    # An admin must exist first, otherwise middleware redirects to /setup.
    ledger.create_user(
        username="admin",
        email="a@a",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.get("/api/topology")
    assert resp.status_code == 401


def test_segment_requires_auth(client):
    ledger.create_user(
        username="admin",
        email="a@a",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.get("/api/topology/segment/10.4.0.0/24")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/topology
# ---------------------------------------------------------------------------


def test_topology_returns_sankey_shape(client):
    _seed_admin_and_login(client)
    _seed_topology_traffic()

    data = client.get("/api/topology?window=24h").json()
    assert data["window"] == "24h"
    assert isinstance(data["nodes"], list)
    assert isinstance(data["links"], list)

    layers = {n["layer"] for n in data["nodes"]}
    assert layers == {"segment", "agent", "gateway", "upstream", "model"}
    # Response advertises the layer order; the middle column is the fixed
    # gateway waypoint that every recorded request passes through.
    assert data["layers"] == ["segment", "agent", "gateway", "upstream", "model"]

    # Exactly one gateway node, always labeled "KYDE Gateway".
    gateway_nodes = [n for n in data["nodes"] if n["layer"] == "gateway"]
    assert len(gateway_nodes) == 1
    assert gateway_nodes[0]["label"] == "KYDE Gateway"
    assert gateway_nodes[0]["id"] == "gateway:kyde-gateway"

    seg_node = next(n for n in data["nodes"] if n["id"] == "seg:10.4.0.0/24")
    assert seg_node["label"] == "10.4.0.0/24"
    assert seg_node["meta"]["class"] == "rfc1918"

    pub_seg = next(n for n in data["nodes"] if n["id"] == "seg:203.0.113.0/24")
    assert pub_seg["meta"]["class"] == "public"

    # Link values are counts — the sum of segment→agent link values for one
    # segment equals the total request count from that segment.
    rfc_links = [link for link in data["links"] if link["source"] == "seg:10.4.0.0/24"]
    assert sum(link["value"] for link in rfc_links) == 3 + 2 + 4

    pub_links = [
        link for link in data["links"] if link["source"] == "seg:203.0.113.0/24"
    ]
    assert sum(link["value"] for link in pub_links) == 1 + 5

    # Every agent connects to the gateway, and the gateway connects to
    # every upstream — no traffic should be dropped on either side.
    agent_to_gw = sum(
        link["value"]
        for link in data["links"]
        if link["target"] == "gateway:kyde-gateway"
    )
    gw_to_up = sum(
        link["value"]
        for link in data["links"]
        if link["source"] == "gateway:kyde-gateway"
    )
    assert agent_to_gw == gw_to_up == 3 + 2 + 4 + 1 + 5


def test_topology_flow_returns_breakdown_for_one_link(client):
    """Flow endpoint filters ledger+request_network to one Sankey link's
    endpoints and returns top agents + recent sessions for that flow."""
    _seed_admin_and_login(client)
    _seed_topology_traffic()

    # agent:10.4.0.0/24:cursor → api.openai.com: the seed has 3 such
    # requests (gpt-4o). Its other 2 requests go to api.anthropic.com
    # and shouldn't be counted here.
    body = client.get(
        "/api/topology/flow?source_layer=agent&source_label=agent:10.4.0.0/24:cursor"
        "&target_layer=upstream&target_label=api.openai.com&window=24h"
    ).json()
    assert body["request_count"] == 3
    assert body["source_layer"] == "agent"
    assert body["target_label"] == "api.openai.com"
    assert len(body["agents"]) > 0


def test_topology_flow_gateway_layer_matches_unconditionally(client):
    """The gateway layer is a fixed waypoint — clicking either side of
    it returns every row that satisfies the other endpoint's filter."""
    _seed_admin_and_login(client)
    _seed_topology_traffic()

    # gateway → api.openai.com: every request to OpenAI in the window.
    # Seed: 3 (10.4 cursor) + 4 (10.4 copilot) + 1 (203.0 cursor) = 8.
    body = client.get(
        "/api/topology/flow?source_layer=gateway&source_label=KYDE+Gateway"
        "&target_layer=upstream&target_label=api.openai.com&window=24h"
    ).json()
    assert body["request_count"] == 3 + 4 + 1


def test_topology_flow_returns_zero_on_no_match(client):
    _seed_admin_and_login(client)
    body = client.get(
        "/api/topology/flow?source_layer=agent&source_label=agent:nonexistent"
        "&target_layer=upstream&target_label=nowhere&window=24h"
    ).json()
    assert body["request_count"] == 0
    assert body["agents"] == []


def test_topology_ip_includes_hostname_from_lazy_resolve(client):
    """/api/topology/ip/{ip} triggers the lazy resolver and returns the
    cached hostname. DNS is mocked so CI doesn't hit a real resolver."""
    from unittest.mock import patch
    from kyde import host_resolver

    _seed_admin_and_login(client)
    e = _append(
        model="gpt-4o", upstream="openai", agent_id="agent:host", session_id="s-host"
    )
    _attach_network(
        e.seq,
        e.timestamp,
        subnet="10.4.0.0/24",
        klass="rfc1918",
        tool="cursor",
        upstream_host="api.openai.com",
        remote="10.4.0.1",
    )

    with patch.object(host_resolver, "_reverse_dns_sync", return_value="crm.internal"):
        body = client.get("/api/topology/ip/10.4.0.1?window=24h").json()

    assert body["hostname"] == "crm.internal"
    assert body["hostname_source"] == "dns"


def test_topology_ip_returns_admin_label_over_dns(client):
    """When an admin has labeled the IP, the response carries the admin
    name regardless of what DNS says."""
    from unittest.mock import patch
    from kyde import host_resolver, ledger

    _seed_admin_and_login(client)
    e = _append(
        model="gpt-4o", upstream="openai", agent_id="agent:host2", session_id="s-host2"
    )
    _attach_network(
        e.seq,
        e.timestamp,
        subnet="10.4.0.0/24",
        klass="rfc1918",
        tool="cursor",
        upstream_host="api.openai.com",
        remote="10.4.0.2",
    )
    ledger.upsert_host_label(ip="10.4.0.2", hostname="canonical.corp")

    with patch.object(
        host_resolver, "_reverse_dns_sync", return_value="should-be-ignored.local"
    ):
        body = client.get("/api/topology/ip/10.4.0.2?window=24h").json()

    assert body["hostname"] == "canonical.corp"
    assert body["hostname_source"] == "admin"


def test_topology_gateway_waypoint_is_fixed_across_uas(client):
    """Regardless of what ua_tool the rows carry — the historical
    'kyde-gateway' positive marker, an empty UA, or the literal 'unknown'
    parser fallback — the Sankey shows exactly one gateway node labeled
    'KYDE Gateway' and routes every agent through it. The UA dimension
    is no longer surfaced on the Sankey (it still lives on the per-host
    and per-agent breakdown endpoints)."""
    _seed_admin_and_login(client)
    for i, tool in enumerate(["kyde-gateway", "", "unknown", "cursor"]):
        e = _append(
            model="gpt-4o",
            upstream="openai",
            agent_id=f"agent:wp-{i}",
            session_id=f"s-wp-{i}",
        )
        _attach_network(
            e.seq,
            e.timestamp,
            subnet="10.4.0.0/24",
            klass="rfc1918",
            tool=tool,
            upstream_host="api.openai.com",
        )

    data = client.get("/api/topology?window=24h").json()

    gateway_nodes = [n for n in data["nodes"] if n["layer"] == "gateway"]
    assert len(gateway_nodes) == 1
    assert gateway_nodes[0]["label"] == "KYDE Gateway"

    # Every distinct agent connects to the single gateway node.
    agent_ids = {n["id"] for n in data["nodes"] if n["layer"] == "agent"}
    targeted_agents = {
        link["source"]
        for link in data["links"]
        if link["target"] == "gateway:kyde-gateway"
    }
    assert targeted_agents == agent_ids


def test_topology_rejects_bad_window(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology?window=2y")
    assert resp.status_code == 400


def test_topology_min_value_filter(client):
    _seed_admin_and_login(client)
    _seed_topology_traffic()

    # min_value=4 drops all combos below 4 requests.
    data = client.get("/api/topology?window=24h&min_value=4").json()
    seg_agent_links = [
        link for link in data["links"] if link["source"].startswith("seg:")
    ]
    # Only 203.0.113.0/24 → agent:…:claude-code (5) and 10.4.0.0/24 →
    # agent:…:copilot (4) survive the filter. Segment→agent values mirror
    # the row counts because each agent_id is unique to one row.
    counts = sorted(link["value"] for link in seg_agent_links)
    assert counts == [4, 5]


def test_topology_empty_when_no_data(client):
    _seed_admin_and_login(client)
    data = client.get("/api/topology?window=24h").json()
    assert data["nodes"] == []
    assert data["links"] == []


# ---------------------------------------------------------------------------
# /api/topology/segment/{subnet}
# ---------------------------------------------------------------------------


def test_segment_detail_returns_agents_ips_sessions(client):
    _seed_admin_and_login(client)
    _seed_topology_traffic()

    # FastAPI's {subnet:path} accepts the slash without URL-encoding.
    resp = client.get("/api/topology/segment/10.4.0.0/24?window=24h")
    assert resp.status_code == 200
    data = resp.json()

    assert data["subnet"] == "10.4.0.0/24"
    assert data["class"] == "rfc1918"
    # 2 tools × 1 session each = 2 sessions under this segment.
    assert len(data["sessions"]) == 2
    # 2 distinct agents (agent:10.4.0.0/24:cursor, ...:copilot)
    assert len(data["agents"]) == 2
    tools_flat = {t for a in data["agents"] for t in a["tools"]}
    assert tools_flat == {"cursor", "copilot"}


def test_segment_detail_rejects_invalid_cidr(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology/segment/not-a-cidr")
    assert resp.status_code == 400


def test_segment_detail_unknown_subnet_is_empty(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology/segment/192.0.2.0/24")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents"] == []
    assert data["ips"] == []
    assert data["sessions"] == []


# ---------------------------------------------------------------------------
# /api/topology/agent/{agent_id}
# ---------------------------------------------------------------------------


def _seed_mobile_agent(agent_id: str = "agent:mobile") -> None:
    """Same agent_id hitting from two different segments — the interesting case
    for the agent view (shows 'where is this agent talking from')."""
    specs = [
        # subnet,           klass,     tool,       remote,         n
        ("10.4.0.0/24", "rfc1918", "cursor", "10.4.0.17", 3),
        ("203.0.113.0/24", "public", "cursor", "203.0.113.5", 2),
    ]
    for seg, klass, tool, remote, n in specs:
        for _ in range(n):
            e = _append(agent_id=agent_id, session_id=f"s-{seg}")
            _attach_network(
                e.seq,
                e.timestamp,
                subnet=seg,
                klass=klass,
                tool=tool,
                upstream_host="api.openai.com",
                remote=remote,
            )


def test_agent_detail_returns_cross_segment_breakdown(client):
    _seed_admin_and_login(client)
    _seed_mobile_agent()

    data = client.get("/api/topology/agent/agent:mobile?window=24h").json()
    assert data["agent_id"] == "agent:mobile"
    assert data["request_count"] == 5

    subnets = {s["subnet"] for s in data["segments"]}
    assert subnets == {"10.4.0.0/24", "203.0.113.0/24"}

    ips = {i["ip"] for i in data["ips"]}
    assert ips == {"10.4.0.17", "203.0.113.5"}

    assert len(data["tools"]) == 1 and data["tools"][0]["tool"] == "cursor"
    assert data["first_seen_iso"] is not None
    assert data["last_seen_iso"] is not None


def test_agent_detail_unknown_agent_returns_empty(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology/agent/agent:does-not-exist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_count"] == 0
    assert data["segments"] == []
    assert data["ips"] == []


def test_agent_detail_requires_auth(client):
    ledger.create_user(
        username="admin",
        email="a@a",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.get("/api/topology/agent/agent:anything")
    assert resp.status_code == 401


def test_agent_detail_rejects_overlong_id(client):
    _seed_admin_and_login(client)
    resp = client.get(f"/api/topology/agent/{'x' * 600}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/topology/ip/{ip}
# ---------------------------------------------------------------------------


def test_ip_detail_returns_agents_tools_sessions(client):
    _seed_admin_and_login(client)
    # Two agents sharing one NAT egress — the IP-view use case.
    shared_ip = "10.4.0.17"
    for agent, tool, n in [
        ("agent:alice", "cursor", 3),
        ("agent:bob", "copilot", 2),
    ]:
        for _ in range(n):
            e = _append(agent_id=agent, session_id=f"s-{agent}")
            _attach_network(
                e.seq,
                e.timestamp,
                subnet="10.4.0.0/24",
                klass="rfc1918",
                tool=tool,
                upstream_host="api.openai.com",
                remote=shared_ip,
            )

    data = client.get(f"/api/topology/ip/{shared_ip}?window=24h").json()
    assert data["ip"] == shared_ip
    assert data["class"] == "rfc1918"
    assert data["subnet"] == "10.4.0.0/24"
    assert data["request_count"] == 5

    agent_ids = {a["agent_id"] for a in data["agents"]}
    assert agent_ids == {"agent:alice", "agent:bob"}

    tools = {t["tool"] for t in data["tools"]}
    assert tools == {"cursor", "copilot"}

    session_ids = {s["session_id"] for s in data["sessions"]}
    assert session_ids == {"s-agent:alice", "s-agent:bob"}


def test_ip_detail_rejects_invalid_ip(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology/ip/not-an-ip")
    assert resp.status_code == 400


def test_ip_detail_unknown_ip_returns_empty(client):
    _seed_admin_and_login(client)
    resp = client.get("/api/topology/ip/198.51.100.42")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_count"] == 0
    assert data["agents"] == []
    assert data["class"] == "unknown"


def test_ip_detail_requires_auth(client):
    ledger.create_user(
        username="admin",
        email="a@a",
        password_hash=auth.hash_password(PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    resp = client.get("/api/topology/ip/10.4.0.17")
    assert resp.status_code == 401
