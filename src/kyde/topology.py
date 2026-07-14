"""
Agent topology API.

Aggregates `request_network` joined to `ledger` into a Sankey-shaped
response (`network segment → agent → KYDE Gateway → upstream → model`)
and a subnet drill-down (`agents`, `ips`, `recent sessions` under one CIDR).

The middle column is a fixed waypoint — every recorded request traverses
the gateway by definition (the gateway is what wrote the row), so it
shows up as a single "KYDE Gateway" node rather than a UA-derived
dimension. UA-derived tool info still lives on the per-host and
per-agent breakdowns served by the drill-down endpoints below.

Auth: these routes sit under `/api/...` and are gated by the global
auth_middleware in dashboard.py — unauthenticated callers get a 401
before the handler runs, same as every other /api/* endpoint.
"""

from __future__ import annotations

import ipaddress
import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Query

from . import ledger
from .dashboard import app


# ---------------------------------------------------------------------------
# Window mapping
# ---------------------------------------------------------------------------


_WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
}

# "all" means no time floor. Kept out of the seconds dict so the topology
# endpoints (which want strict time-window behavior) can opt out of it.
WINDOW_ALL = "all"
TIMED_WINDOWS = set(_WINDOW_SECONDS)
ALL_WINDOWS = TIMED_WINDOWS | {WINDOW_ALL}

# Sankey layer → SQL column expression. Used by the flow-detail endpoint
# to build a WHERE predicate matching either endpoint of a Sankey link.
# Kept symmetrical with the SELECT in api_topology so the labels coming
# back from the frontend always have a matching column to filter on.
# The "gateway" layer is intentionally absent — it's a fixed waypoint,
# not a SQL column, and `_layer_filter` short-circuits to a no-op match.
_LAYER_COLUMNS: dict[str, str] = {
    "segment": "rn.origin_subnet",
    "agent": "l.agent_id",
    "upstream": "COALESCE(NULLIF(rn.upstream_host, ''), l.upstream)",
    "model": "l.model",
}


def _layer_filter(layer: str, label: str) -> tuple[str, list]:
    """Return (sql_fragment, params) that filters ledger rows whose Sankey
    column for `layer` equals `label`. The "gateway" layer is a fixed
    waypoint that every recorded row passes through, so it matches
    unconditionally.
    """
    if layer == "gateway":
        return ("1=1", [])
    col = _LAYER_COLUMNS.get(layer)
    if col is None:
        raise HTTPException(status_code=400, detail=f"unsupported layer {layer!r}")
    return (f"{col} = %s", [label])


def _window_floor(window: str) -> float:
    """Strict variant: requires a timed window (1h, 24h, 7d, 30d, 90d).
    Used by topology endpoints where 'all' would be too large to chart."""
    seconds = _WINDOW_SECONDS.get(window)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported window {window!r}; expected one of {sorted(TIMED_WINDOWS)}",
        )
    return time.time() - seconds


def window_floor_or_none(window: str) -> float | None:
    """Lenient variant: accepts 'all' (returns None — caller should skip the
    timestamp WHERE clause). Used by /api/stats and /api/token-analysis
    where the operator may legitimately want every row."""
    if window == WINDOW_ALL:
        return None
    return _window_floor(window)


# ---------------------------------------------------------------------------
# /api/topology — the Sankey feed
# ---------------------------------------------------------------------------


GATEWAY_NODE_ID = "gateway:kyde-gateway"
GATEWAY_NODE_LABEL = "KYDE Gateway"


@app.get("/api/topology")
def api_topology(
    window: str = Query("24h"),
    min_value: int = Query(1, ge=1, le=1000),
):
    """Return a five-layer Sankey (segment → agent → KYDE Gateway → upstream → model).

    The middle column is a fixed waypoint: every recorded request is by
    definition mediated by kyde-gateway, so the Sankey shows it as one
    node rather than a UA-derived dimension. UA-derived tool info still
    surfaces on the per-host and per-agent drill-downs below.

    The agent layer joins `agents.display_name` when set so node labels
    show the human-readable name; otherwise the agent_id falls through.
    """
    floor = _window_floor(window)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(NULLIF(rn.origin_subnet, ''), 'unknown')         AS segment,
                    COALESCE(NULLIF(rn.origin_class,  ''), 'unknown')         AS segment_class,
                    COALESCE(NULLIF(l.agent_id,       ''), 'unknown')         AS agent_id,
                    COALESCE(NULLIF(a.display_name,   ''), l.agent_id, 'unknown') AS agent_label,
                    COALESCE(NULLIF(rn.upstream_host, ''), l.upstream, 'unknown') AS upstream,
                    COALESCE(NULLIF(l.model, ''), 'unknown')                  AS model,
                    COUNT(*) AS n
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                  LEFT JOIN agents a ON a.agent_id = l.agent_id
                 WHERE rn.timestamp >= %s
                 GROUP BY 1, 2, 3, 4, 5, 6
                HAVING COUNT(*) >= %s
                 ORDER BY n DESC
                 LIMIT 500
                """,
                (floor, min_value),
            )
            rows = list(cur.fetchall())

    nodes: dict[str, dict] = {}
    links: dict[tuple[str, str], int] = {}

    def _ensure(node_id: str, layer: str, label: str, meta: Optional[dict] = None):
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "layer": layer, "label": label}
            if meta:
                nodes[node_id]["meta"] = meta

    def _link(src: str, dst: str, value: int):
        key = (src, dst)
        links[key] = links.get(key, 0) + value

    for r in rows:
        n = int(r["n"])
        seg = str(r["segment"])
        seg_cls = str(r["segment_class"])
        agent_id = str(r["agent_id"])
        agent_label = str(r["agent_label"])
        upstream = str(r["upstream"])
        model = str(r["model"])

        seg_id = f"seg:{seg}"
        agent_node_id = f"agent:{agent_id}"
        up_id = f"up:{upstream}"
        model_id = f"model:{model}"

        _ensure(seg_id, "segment", seg, {"class": seg_cls})
        _ensure(agent_node_id, "agent", agent_label, {"agent_id": agent_id})
        _ensure(GATEWAY_NODE_ID, "gateway", GATEWAY_NODE_LABEL)
        _ensure(up_id, "upstream", upstream)
        _ensure(model_id, "model", model)

        _link(seg_id, agent_node_id, n)
        _link(agent_node_id, GATEWAY_NODE_ID, n)
        _link(GATEWAY_NODE_ID, up_id, n)
        _link(up_id, model_id, n)

    return {
        "window": window,
        "min_value": min_value,
        "layers": ["segment", "agent", "gateway", "upstream", "model"],
        "nodes": list(nodes.values()),
        "links": [
            {"source": s, "target": t, "value": v}
            for (s, t), v in sorted(links.items(), key=lambda kv: -kv[1])
        ],
    }


# ---------------------------------------------------------------------------
# /api/topology/segment/{subnet} — drill-down
# ---------------------------------------------------------------------------


def _validate_subnet(subnet: str) -> str:
    """Normalize + validate a CIDR input before it hits SQL."""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid CIDR: {exc}") from exc
    return str(net)


@app.get("/api/topology/segment/{subnet:path}")
def api_topology_segment(
    subnet: str,
    window: str = Query("24h"),
):
    """Agents, IPs, and recent sessions observed under one origin_subnet."""
    cidr = _validate_subnet(subnet)
    floor = _window_floor(window)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            # Segment class — pick any row's class (they should all agree
            # within a subnet since class is a function of the CIDR's IPs).
            cur.execute(
                """
                SELECT origin_class
                  FROM request_network
                 WHERE origin_subnet = %s
                   AND timestamp >= %s
                 LIMIT 1
                """,
                (cidr, floor),
            )
            klass_row = cur.fetchone()
            seg_class = klass_row["origin_class"] if klass_row else "unknown"

            cur.execute(
                """
                SELECT l.agent_id,
                       COUNT(*)                              AS request_count,
                       MIN(l.timestamp)                      AS first_seen,
                       MAX(l.timestamp)                      AS last_seen,
                       array_agg(DISTINCT NULLIF(rn.ua_tool, '')) FILTER (
                           WHERE rn.ua_tool IS NOT NULL AND rn.ua_tool <> ''
                       )                                     AS tools,
                       array_agg(DISTINCT NULLIF(l.upstream, '')) FILTER (
                           WHERE l.upstream IS NOT NULL AND l.upstream <> ''
                       )                                     AS upstreams
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE rn.origin_subnet = %s
                   AND rn.timestamp    >= %s
                 GROUP BY l.agent_id
                 ORDER BY request_count DESC
                 LIMIT 100
                """,
                (cidr, floor),
            )
            agents = [
                {
                    "agent_id": r["agent_id"],
                    "request_count": int(r["request_count"]),
                    "first_seen": float(r["first_seen"]),
                    "last_seen": float(r["last_seen"]),
                    "first_seen_iso": datetime.fromtimestamp(
                        float(r["first_seen"])
                    ).isoformat(),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                    "tools": list(r["tools"] or []),
                    "upstreams": list(r["upstreams"] or []),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT rn.remote_addr AS ip,
                       COUNT(*)       AS request_count,
                       COALESCE(
                           (array_agg(rn.ua_tool) FILTER (WHERE rn.ua_tool <> ''))[1],
                           ''
                       )              AS ua_tool
                  FROM request_network rn
                 WHERE rn.origin_subnet = %s
                   AND rn.timestamp    >= %s
                   AND rn.remote_addr IS NOT NULL
                 GROUP BY rn.remote_addr
                 ORDER BY request_count DESC
                 LIMIT 100
                """,
                (cidr, floor),
            )
            ips = [
                {
                    "ip": str(r["ip"]),
                    "request_count": int(r["request_count"]),
                    "ua_tool": r["ua_tool"] or "",
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT l.session_id,
                       COUNT(*)                               AS request_count,
                       MAX(l.timestamp)                       AS last_seen,
                       COALESCE(
                           (array_agg(l.model) FILTER (WHERE l.model <> ''))[1],
                           ''
                       )                                      AS model
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE rn.origin_subnet = %s
                   AND rn.timestamp    >= %s
                   AND l.session_id    <> ''
                 GROUP BY l.session_id
                 ORDER BY last_seen DESC
                 LIMIT 50
                """,
                (cidr, floor),
            )
            sessions = [
                {
                    "session_id": r["session_id"],
                    "request_count": int(r["request_count"]),
                    "last_seen": float(r["last_seen"]),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                    "model": r["model"] or "",
                }
                for r in cur.fetchall()
            ]

    return {
        "subnet": cidr,
        "class": seg_class,
        "window": window,
        "agents": agents,
        "ips": ips,
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# /api/topology/agent/{agent_id} — single-agent drill-down
# ---------------------------------------------------------------------------


def _count_breakdown_rows(rows) -> list[dict]:
    """Shared decoder for `SELECT <label>, COUNT(*) AS n` queries."""
    out: list[dict] = []
    for r in rows:
        key = next((k for k in r.keys() if k != "n"), None)
        if key is None:
            continue
        value = r[key]
        if value is None or value == "":
            continue
        out.append({key: value, "request_count": int(r["n"])})
    return out


@app.get("/api/topology/agent/{agent_id:path}")
def api_topology_agent(
    agent_id: str,
    window: str = Query("24h"),
):
    """Per-agent summary: segments, IPs, tools, upstreams, models, sessions."""
    if not agent_id or len(agent_id) > 500:
        raise HTTPException(status_code=400, detail="agent_id missing or too long")
    floor = _window_floor(window)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(l.timestamp) AS first_seen,
                       MAX(l.timestamp) AS last_seen,
                       COUNT(*)         AS request_count
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 WHERE l.agent_id      = %s
                   AND rn.timestamp   >= %s
                """,
                (agent_id, floor),
            )
            summary = cur.fetchone() or {}
            total = int(summary.get("request_count") or 0)

            if total == 0:
                return {
                    "agent_id": agent_id,
                    "window": window,
                    "request_count": 0,
                    "first_seen": None,
                    "first_seen_iso": None,
                    "last_seen": None,
                    "last_seen_iso": None,
                    "segments": [],
                    "ips": [],
                    "tools": [],
                    "upstreams": [],
                    "models": [],
                    "sessions": [],
                }

            first_seen = float(summary["first_seen"])
            last_seen = float(summary["last_seen"])

            cur.execute(
                """
                SELECT COALESCE(NULLIF(rn.origin_subnet, ''), 'unknown') AS subnet,
                       MAX(COALESCE(NULLIF(rn.origin_class, ''), 'unknown')) AS class,
                       COUNT(*) AS n
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE l.agent_id    = %s
                   AND rn.timestamp >= %s
                 GROUP BY 1
                 ORDER BY n DESC
                """,
                (agent_id, floor),
            )
            segments = [
                {
                    "subnet": r["subnet"],
                    "class": r["class"],
                    "request_count": int(r["n"]),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT rn.remote_addr AS ip,
                       COUNT(*)       AS n,
                       COALESCE(
                           (array_agg(rn.ua_tool) FILTER (WHERE rn.ua_tool <> ''))[1],
                           ''
                       )              AS ua_tool
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE l.agent_id       = %s
                   AND rn.timestamp    >= %s
                   AND rn.remote_addr IS NOT NULL
                 GROUP BY rn.remote_addr
                 ORDER BY n DESC
                 LIMIT 100
                """,
                (agent_id, floor),
            )
            # Decorate each IP with its cached hostname (zero-DNS-call —
            # read-only from host_resolutions). Fresh dns rows + admin
            # rows surface their hostname; stale or absent rows return
            # None and the UI renders the bare IP.
            from . import host_resolver

            ip_rows = cur.fetchall()
            ips = []
            for r in ip_rows:
                ip_str = str(r["ip"])
                cached = host_resolver.get_cached(ip_str)
                ips.append(
                    {
                        "ip": ip_str,
                        "hostname": cached.hostname if cached else None,
                        "hostname_source": cached.source if cached else None,
                        "request_count": int(r["n"]),
                        "ua_tool": r["ua_tool"] or "",
                    }
                )

            cur.execute(
                """
                SELECT COALESCE(NULLIF(rn.ua_tool, ''), 'unknown') AS tool,
                       COUNT(*) AS n
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE l.agent_id    = %s
                   AND rn.timestamp >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (agent_id, floor),
            )
            tools = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT COALESCE(NULLIF(rn.upstream_host,''), l.upstream, 'unknown')
                           AS upstream,
                       COUNT(*) AS n
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE l.agent_id    = %s
                   AND rn.timestamp >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (agent_id, floor),
            )
            upstreams = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT COALESCE(NULLIF(l.model, ''), 'unknown') AS model,
                       COUNT(*) AS n
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 WHERE l.agent_id    = %s
                   AND rn.timestamp >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (agent_id, floor),
            )
            models = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT l.session_id,
                       COUNT(*)          AS n,
                       MAX(l.timestamp)  AS last_seen,
                       COALESCE(
                           (array_agg(l.model) FILTER (WHERE l.model <> ''))[1],
                           ''
                       )                 AS model
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 WHERE l.agent_id    = %s
                   AND rn.timestamp >= %s
                   AND l.session_id <> ''
                 GROUP BY l.session_id
                 ORDER BY last_seen DESC
                 LIMIT 50
                """,
                (agent_id, floor),
            )
            sessions = [
                {
                    "session_id": r["session_id"],
                    "request_count": int(r["n"]),
                    "last_seen": float(r["last_seen"]),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                    "model": r["model"] or "",
                }
                for r in cur.fetchall()
            ]

    return {
        "agent_id": agent_id,
        "window": window,
        "request_count": total,
        "first_seen": first_seen,
        "first_seen_iso": datetime.fromtimestamp(first_seen).isoformat(),
        "last_seen": last_seen,
        "last_seen_iso": datetime.fromtimestamp(last_seen).isoformat(),
        "segments": segments,
        "ips": ips,
        "tools": tools,
        "upstreams": upstreams,
        "models": models,
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# /api/topology/ip/{ip} — single-IP drill-down
# ---------------------------------------------------------------------------


def _validate_ip(ip_str: str) -> str:
    try:
        return str(ipaddress.ip_address(ip_str))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid IP: {exc}") from exc


@app.get("/api/topology/ip/{ip}")
async def api_topology_ip(
    ip: str,
    window: str = Query("24h"),
):
    """Per-IP summary: agents, tools, upstreams, models, sessions seen from one `remote_addr`.

    Also triggers the lazy hostname resolver (`host_resolver.resolve_and_cache`)
    so the page always knows the current hostname for this IP. First call
    for a never-resolved IP pays the DNS round-trip; subsequent calls are
    O(1) DB lookups until the TTL expires.
    """
    from . import host_resolver

    normalized = _validate_ip(ip)
    floor = _window_floor(window)
    # Fire the resolver before the SQL — the DNS lookup runs in a thread
    # pool, so it doesn't block the cursor work that follows.
    resolution = await host_resolver.resolve_and_cache(normalized)

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(rn.timestamp)             AS first_seen,
                       MAX(rn.timestamp)             AS last_seen,
                       COUNT(*)                      AS request_count,
                       MAX(rn.origin_class)          AS origin_class,
                       MAX(rn.origin_subnet)         AS origin_subnet
                  FROM request_network rn
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                """,
                (normalized, floor),
            )
            summary = cur.fetchone() or {}
            total = int(summary.get("request_count") or 0)

            if total == 0:
                return {
                    "ip": normalized,
                    "hostname": resolution.hostname,
                    "hostname_source": resolution.source,
                    "class": "unknown",
                    "subnet": "",
                    "window": window,
                    "request_count": 0,
                    "first_seen": None,
                    "first_seen_iso": None,
                    "last_seen": None,
                    "last_seen_iso": None,
                    "agents": [],
                    "tools": [],
                    "upstreams": [],
                    "models": [],
                    "sessions": [],
                }

            first_seen = float(summary["first_seen"])
            last_seen = float(summary["last_seen"])

            cur.execute(
                """
                SELECT l.agent_id,
                       COUNT(*)          AS n,
                       MIN(l.timestamp)  AS first_seen,
                       MAX(l.timestamp)  AS last_seen,
                       array_agg(DISTINCT NULLIF(rn.ua_tool, '')) FILTER (
                           WHERE rn.ua_tool IS NOT NULL AND rn.ua_tool <> ''
                       )                 AS tools
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                 GROUP BY l.agent_id
                 ORDER BY n DESC
                 LIMIT 50
                """,
                (normalized, floor),
            )
            agents = [
                {
                    "agent_id": r["agent_id"],
                    "request_count": int(r["n"]),
                    "first_seen": float(r["first_seen"]),
                    "last_seen": float(r["last_seen"]),
                    "first_seen_iso": datetime.fromtimestamp(
                        float(r["first_seen"])
                    ).isoformat(),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                    "tools": list(r["tools"] or []),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT COALESCE(NULLIF(rn.ua_tool, ''), 'unknown') AS tool,
                       COUNT(*) AS n
                  FROM request_network rn
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (normalized, floor),
            )
            tools = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT COALESCE(NULLIF(rn.upstream_host,''), l.upstream, 'unknown')
                           AS upstream,
                       COUNT(*) AS n
                  FROM request_network rn
                  JOIN ledger l USING (seq)
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (normalized, floor),
            )
            upstreams = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT COALESCE(NULLIF(l.model, ''), 'unknown') AS model,
                       COUNT(*) AS n
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                 GROUP BY 1 ORDER BY n DESC
                """,
                (normalized, floor),
            )
            models = _count_breakdown_rows(cur.fetchall())

            cur.execute(
                """
                SELECT l.session_id,
                       COUNT(*)          AS n,
                       MAX(l.timestamp)  AS last_seen,
                       COALESCE(
                           (array_agg(l.model) FILTER (WHERE l.model <> ''))[1],
                           ''
                       )                 AS model
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 WHERE rn.remote_addr = %s::inet
                   AND rn.timestamp  >= %s
                   AND l.session_id <> ''
                 GROUP BY l.session_id
                 ORDER BY last_seen DESC
                 LIMIT 50
                """,
                (normalized, floor),
            )
            sessions = [
                {
                    "session_id": r["session_id"],
                    "request_count": int(r["n"]),
                    "last_seen": float(r["last_seen"]),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                    "model": r["model"] or "",
                }
                for r in cur.fetchall()
            ]

    return {
        "ip": normalized,
        "hostname": resolution.hostname,
        "hostname_source": resolution.source,
        "class": str(summary["origin_class"] or "unknown"),
        "subnet": str(summary["origin_subnet"] or ""),
        "window": window,
        "request_count": total,
        "first_seen": first_seen,
        "first_seen_iso": datetime.fromtimestamp(first_seen).isoformat(),
        "last_seen": last_seen,
        "last_seen_iso": datetime.fromtimestamp(last_seen).isoformat(),
        "agents": agents,
        "tools": tools,
        "upstreams": upstreams,
        "models": models,
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# /api/topology/flow — drill-down for a single Sankey link
# ---------------------------------------------------------------------------


@app.get("/api/topology/flow")
def api_topology_flow(
    source_layer: str = Query(...),
    source_label: str = Query(...),
    target_layer: str = Query(...),
    target_label: str = Query(...),
    window: str = Query("24h"),
):
    """Drill-down for one Sankey link.

    Filters ledger+request_network to rows where the source layer's column
    equals source_label AND the target layer's column equals target_label
    within `window`. Returns the total request count, top agents, and
    recent sessions for that flow.

    Used by the Network Map side panel when the user clicks a link.
    """
    floor = _window_floor(window)
    src_sql, src_params = _layer_filter(source_layer, source_label)
    tgt_sql, tgt_params = _layer_filter(target_layer, target_label)

    where_sql = f"WHERE rn.timestamp >= %s AND {src_sql} AND {tgt_sql}"
    base_params = [floor, *src_params, *tgt_params]

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS n,
                       MIN(rn.timestamp) AS first_seen,
                       MAX(rn.timestamp) AS last_seen
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                 {where_sql}
                """,
                base_params,
            )
            summary = cur.fetchone() or {}
            total = int(summary.get("n") or 0)
            first_seen = float(summary.get("first_seen") or 0)
            last_seen = float(summary.get("last_seen") or 0)

            if total == 0:
                return {
                    "source_layer": source_layer,
                    "source_label": source_label,
                    "target_layer": target_layer,
                    "target_label": target_label,
                    "window": window,
                    "request_count": 0,
                    "first_seen_iso": None,
                    "last_seen_iso": None,
                    "agents": [],
                    "sessions": [],
                }

            cur.execute(
                f"""
                SELECT l.agent_id,
                       a.display_name,
                       COUNT(*) AS request_count,
                       MAX(rn.timestamp) AS last_seen
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                  LEFT JOIN agents a ON a.agent_id = l.agent_id
                 {where_sql}
                 GROUP BY l.agent_id, a.display_name
                 ORDER BY request_count DESC
                 LIMIT 10
                """,
                base_params,
            )
            agents = [
                {
                    "agent_id": r["agent_id"],
                    "display_name": r["display_name"],
                    "request_count": int(r["request_count"]),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT l.session_id,
                       s.serial_id,
                       MAX(rn.timestamp) AS last_seen,
                       COUNT(*) AS request_count
                  FROM ledger l
                  JOIN request_network rn USING (seq)
                  LEFT JOIN sessions s ON s.session_id = l.session_id
                 {where_sql}
                   AND l.session_id <> ''
                 GROUP BY l.session_id, s.serial_id
                 ORDER BY last_seen DESC
                 LIMIT 10
                """,
                base_params,
            )
            sessions = [
                {
                    "session_id": r["session_id"],
                    "serial_id": (
                        int(r["serial_id"]) if r["serial_id"] is not None else None
                    ),
                    "request_count": int(r["request_count"]),
                    "last_seen_iso": datetime.fromtimestamp(
                        float(r["last_seen"])
                    ).isoformat(),
                }
                for r in cur.fetchall()
            ]

    return {
        "source_layer": source_layer,
        "source_label": source_label,
        "target_layer": target_layer,
        "target_label": target_label,
        "window": window,
        "request_count": total,
        "first_seen_iso": datetime.fromtimestamp(first_seen).isoformat(),
        "last_seen_iso": datetime.fromtimestamp(last_seen).isoformat(),
        "agents": agents,
        "sessions": sessions,
    }
