# MCP Routing — v1 Implementation Plan

## Status (2026-05-26)

**Shipped:** M0 (foundation) and M1 (first provider end-to-end). End-to-end smoke
verified against httpbin: agent → `:4000/mcp/{name}` → nginx → kyde-gateway →
upstream, with `Authorization` forwarded unchanged and `X-Agent-ID` stripped.
Dashboard CRUD + probe-tools endpoint working. Commits: `48a4613` (proxy core),
`10a628b` (registry + dashboard UI).

**Next up:** M2 (signing + DLP). Everything M3–M5 is unchanged from the original plan.

**Files that exist now** (read before editing — picking up cold):
- `src/kyde/mcp_proxy.py` — JSON-RPC parse + Streamable HTTP forward, header rules
- `src/kyde/mcp_registry.py` — per-tenant registry, 5s per-process cache
- `src/kyde/migrations/NNNN_mcp_routing.sql` — `mcp_servers`, `mcp_tool_policies` tables
- `src/kyde/server.py` lines 731-743 — `/mcp/{server_name}` route mount on `_proxy_app`
- `src/kyde/dashboard.py` (search for `mcp`) — `/api/mcp/servers` CRUD + `/probe-tools`
- `tests/test_mcp_registry.py`, `tests/test_mcp_proxy.py`, `tests/test_mcp_dashboard.py`
- `frontend/src/pages/mcp-servers.tsx`, `frontend/src/components/shared/mcp-server-dialog.tsx`
- `frontend/nginx.conf` — `/mcp/<name>` location block on the `:4000` agent-facing surface

**Non-obvious behaviour from the M1 build that will bite future work:**

1. **The registry cache is per-process with a 5s TTL** (mirrors `settings.py`).
   Dashboard PATCH invalidates the *dashboard's* cache; the gateway process sees
   the change within 5s, not instantly. Don't chase "the toggle isn't sticking"
   bug reports — wait 5s and retry. If real cross-process invalidation becomes
   needed, that's a Postgres LISTEN/NOTIFY change, not a registry rewrite.

2. **`/mcp/*` only reaches the gateway via `:4000` because nginx (kyde-ui) was
   updated to allowlist it.** The agent-facing surface has a path allowlist that
   404s anything outside `/v1/*`, `/<provider>/v1/*`, `/mcp/<name>`. If you add
   another agent-facing route (e.g. the M4 aggregator at bare `/mcp/`), update
   `frontend/nginx.conf` *and rebuild kyde-ui*, not just FastAPI.

3. **`mcp_proxy.handle_mcp_request` uses `httpx.AsyncClient` constructed inline.**
   Tests monkeypatch `mcp_proxy.httpx.AsyncClient` to a factory returning a
   `_FakeAsyncClient` — see `tests/test_mcp_proxy.py` for the pattern. The probe
   endpoint uses `client.post()` while the proxy uses `client.request()`; the
   probe's fake must implement `post()` separately (see `_FakeProbeClient` in
   `tests/test_mcp_dashboard.py`).

4. **The probe endpoint forwards the operator's one-off bearer and never stores
   it.** Don't be tempted to cache it "for next time" — that violates the
   transparency-on-upstream-auth contract that the rest of the plan rests on.

5. **`DEFAULT_TENANT = "default"`** is hardcoded in `mcp_registry.py`. When the
   hybrid SaaS work lands (see `project_saas_direction` memory), this becomes
   the tenant-from-network-position lookup — no backfill needed because every
   existing row is already tagged with the default tenant.

**Test commands** (with the stack up via `docker compose up -d`):
```bash
# Backend tests
docker exec kyde-api pytest tests/test_mcp_registry.py tests/test_mcp_proxy.py tests/test_mcp_dashboard.py -v

# Frontend: types + bundle (no test runner in this repo)
cd frontend && npm run build
```

**Smoke recipe** (full end-to-end, requires admin cookie):
```bash
# 1. Create admin if needed (records temp password)
docker exec kyde-api kyde admin create-admin --username smoke --email s@l
# 2. Login (303 → /change-password), POST /api/change-password, then exercise:
curl -b cookies -X POST :8501/api/mcp/servers -d '{"name":"smoke","upstream_url":"https://httpbin.org/post"}'
curl -X POST :4000/mcp/smoke -H 'Authorization: Bearer test' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## Context

The gateway should mediate Model Context Protocol traffic alongside chat completions. MCP `tools/call` and `resources/read` results are where raw business data crosses the AI boundary — file contents, DB query results, customer records — so DLP and audit signing matter at least as much here as for chat prompts.

This plan covers the v1 build: **Streamable HTTP transport only**, **egress proxy plus aggregator endpoint plus per-tenant registry**, with **JSON-walk DLP**, **per-tool policy**, and **ledger entry per JSON-RPC request**. The gateway is **transparent on upstream auth** — agents (or their orchestration framework) inject the `Authorization` header, the gateway forwards it unchanged; credential handling is deliberately out of scope. Ecosystem coverage with this shape is essentially total for vendor-maintained remote MCP servers — Notion, GitHub, Google's `*.googleapis.com/mcp`, Microsoft's `mcp.dev.azure.com`, Atlassian, Slack, HubSpot, Linear, Salesforce, Stripe, Shopify, Cloudflare, Vercel, Sentry — so this scope is the right cut.

## Goals

By the end of v1, a tenant operator can:

1. Register an upstream MCP server in the dashboard (name + URL) and see it appear in the gateway's registry.
2. Point an agent at the gateway's aggregator endpoint and call any tool from any registered MCP server in that tenant. The agent supplies the upstream credential as it would when calling the MCP server directly; the gateway forwards `Authorization` unchanged.
3. See per-tool allow/deny policy enforced on every call, per agent.
4. Find every `tools/call` and `resources/read` as a signed ledger entry attributed to the calling agent and the upstream MCP server.
5. Find DLP findings on tool params and tool results, using the same alert/triage flow as chat DLP.

Validated end-to-end against at least **three** real providers chosen to exercise different upstream auth shapes from the agent side: Notion (OAuth bearer token from the orchestration layer), GitHub (installation token), one self-hosted bearer-token server.

## Non-goals (explicit deferrals)

- **Gateway-managed upstream credentials.** No OAuth flows in the dashboard, no token vaulting, no token refresh. The gateway forwards whatever `Authorization` header the agent sends. Credential lifecycle stays with the agent's orchestration framework / secret manager.
- **HTTP+SSE transport proxying.** Deferred until a customer asks. Atlassian's June 30, 2026 SSE sunset reduces the cost further.
- **stdio→HTTP bridge.** Local-only tools are out of scope for a network gateway.
- **`sampling/createMessage`** (server→client requests for LLM calls). Pass-through stub only; full handling is v1.5.
- **MCP subscriptions / notification fanout.**
- **MCP Apps interactive UI surface** (Amplitude, Asana, Box, Canva, Clay, Figma, Hex, Monday, Slack, Salesforce). v1.5.
- **Cross-tenant aggregator.** Per-tenant only.
- **Runtime discovery (mDNS, K8s annotations).** Explicit registration only.
- **Per-tool human-approval workflows.** Future hardening.

## Approach

Seven workstreams, ordered roughly by dependency. Workstreams 1–2 are foundational and gate everything else; 3–7 can parallelize once the proxy can route a request end-to-end.

### 1. JSON-RPC parsing + Streamable HTTP proxy core

New module `src/kyde/mcp_proxy.py`. Accepts inbound HTTP POST containing JSON-RPC payloads, parses the envelope, extracts `method` / `params` / `id`, identifies the target backend via the registry (workstream 2), forwards over Streamable HTTP, returns the response.

```python
@dataclass
class McpRequest:
    method: str           # e.g. "tools/call"
    params: dict          # method-specific
    id: str | int | None  # JSON-RPC request id; None for notifications
    raw: bytes            # original body for signing

async def proxy_mcp(req: McpRequest, backend: McpBackend) -> McpResponse:
    # 1. policy check (workstream 4)
    # 2. DLP on params (workstream 3)
    # 3. forward to backend — pass through the agent's Authorization header unchanged
    # 4. DLP on result (workstream 3)
    # 5. sign ledger entry (workstream 5)
    # 6. return response
```

The gateway is transparent on upstream auth: whatever `Authorization` (or vendor-specific equivalent) header the agent sends is forwarded to the backend as-is. The gateway never stores, refreshes, or rewrites it. Hop-by-hop headers and Kyde-internal headers are stripped on the way out per the existing chat-proxy rules.

Streamable HTTP detail: the proxy must handle both the plain HTTP response case and the SSE upgrade case where the upstream chooses to stream events for one logical response. For v1, **buffer the full result** before applying DLP and returning (buffer, accept latency, revisit on customer complaint).

Mount point: a new route prefix `/mcp/` on the existing FastAPI app in `src/kyde/server.py`. Path shape `/mcp/{server_name}` routes to a specific backend; `/mcp/` (no name) routes to the aggregator (workstream 6).

### 2. Per-tenant MCP server registry (DB + API + UI)

DB schema (new migration in `src/kyde/migrations/`):

```sql
CREATE TABLE mcp_servers (
    id             UUID PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    name           TEXT NOT NULL,              -- routing handle: "notion", "github"
    upstream_url   TEXT NOT NULL,              -- "https://mcp.notion.com/mcp"
    enabled        BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     INTEGER REFERENCES users(id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE mcp_tool_policies (
    server_id      UUID NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    agent_id       TEXT NOT NULL,              -- or "*" for tenant-wide default
    tool_name      TEXT NOT NULL,              -- or "*" for all tools
    decision       TEXT NOT NULL,              -- "allow" | "deny"
    reason         TEXT,                       -- optional human note
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     INTEGER REFERENCES users(id),
    PRIMARY KEY (server_id, agent_id, tool_name)
);
```

The registry is a pure routing table — no credential columns, no encrypted blobs. Upstream auth is whatever the agent puts on the request.

Backend module `src/kyde/mcp_registry.py`:
- `list_servers(tenant_id)`, `get_server(tenant_id, name)`, `upsert_server(...)`, `delete_server(...)`.
- Cache per-process for 5s, invalidate on write — same shape as `settings.py` resolver.

Admin API endpoints (in `src/kyde/dashboard.py`):
- `GET /api/mcp/servers` — list registered servers for current tenant
- `POST /api/mcp/servers` — register a new one (`name`, `upstream_url`)
- `PATCH /api/mcp/servers/{id}` — rename / change URL / toggle enabled
- `DELETE /api/mcp/servers/{id}`
- `GET /api/mcp/servers/{id}/tools` — proxied `tools/list` so the UI can render policy controls (requires the caller to supply an `Authorization` header for the upstream, just like an agent would)

UI: new "MCP Servers" section in the dashboard (frontend). List view with per-server status (enabled, last-call, last-error), per-server detail with tool list and per-agent policy matrix. "Add MCP Server" wizard flow: enter `name` + `upstream_url` → done. Documentation links explaining that the operator must configure the upstream credential in their agent layer / secret manager — not here.

### 3. DLP JSON-walk engine

New module `src/kyde/dlp_json_walk.py`. Generic recursive walker that visits every string leaf in a JSON value and runs the existing regex/classifier rule set from `dlp.py`. Method-specific entry points:

```python
def scan_tool_call_params(payload: dict) -> list[Finding]:
    # tools/call params look like:
    #   {"name": "...", "arguments": {...}}
    # Walk arguments only; "name" is not user data.

def scan_tool_call_result(payload: dict) -> list[Finding]:
    # tools/call result has "content": [{"type": "text"|"image"|..., ...}]
    # Walk text content; skip binary references for now.

def scan_resource_read_result(payload: dict) -> list[Finding]:
    # resources/read returns "contents": [{"text": "...", "mimeType": "..."}]
    # Walk text content.
```

For methods we don't have a specific extractor for (`prompts/get`, `completion/complete`, etc.), the proxy applies a default full-walk on `params` and `result`. Slower, safer. Profile after v1 ships to identify hot paths worth specializing.

Rules themselves: reuse `dlp.py`'s `_scan_regex` and `_scan_bert` against the strings the walker yields. No new rule format.

Findings emitted into the same `dlp_findings` table chat DLP uses, with new fields:
- `source_type = "mcp"`
- `mcp_method` (e.g., `tools/call`)
- `mcp_tool_name` (e.g., `notion-search`)
- `mcp_server_id`

This makes MCP findings show up in the existing DLP triage UI without UI changes for v1 (just a new filter chip).

### 4. Per-tool policy enforcement

In `mcp_proxy.py` before forwarding:

```python
def check_policy(server_id, agent_id, tool_name) -> Decision:
    # Most specific wins:
    #   (server, agent, tool) > (server, *, tool) > (server, agent, *) > (server, *, *)
    # No explicit row → default allow (configurable per-tenant later).
```

Policy lookup uses the cached `mcp_tool_policies` from workstream 2. Denied calls return a JSON-RPC error (`error.code = -32001`, `error.message = "denied by policy"`) and emit a ledger entry with `outcome = "blocked"` so the audit trail captures the attempt.

UI: per-server detail page in the dashboard renders a matrix of `agents × tools` with allow/deny toggles. Default view shows the `*` row for tenant-wide defaults; expand to set per-agent overrides. Bulk "deny all then allow listed" template for high-risk servers.

### 5. Ledger entries for MCP

Extend `signing.py` (keeping the existing byte-level signing contract) to accept a new entry type:

```python
@dataclass
class McpLedgerEntry:
    entry_type: Literal["mcp_call"]
    tenant_id: str
    agent_id: str
    deploy_id: str
    mcp_server_id: str
    method: str
    tool_name: str | None
    params_hash: str      # sha256 of canonical params JSON
    result_hash: str      # sha256 of canonical result JSON
    outcome: Literal["ok", "blocked", "upstream_error", "dlp_blocked"]
    started_at: datetime
    duration_ms: int
    dlp_finding_ids: list[str]
```

Same Ed25519 signing as chat entries. Same byte-level canonicalization. New entry type in the `ledger_entries` table or a sibling `mcp_ledger_entries` table — recommend **same table** with a `kind` column to keep the export and verification path single.

### 6. Aggregator endpoint

New module `src/kyde/mcp_aggregator.py`. Exposes one MCP endpoint at `/mcp/` (no server name) that:

- On `tools/list`: fans out to every enabled server in the tenant's registry, namespaces tool names as `{server_name}__{tool_name}`, returns the union.
- On `tools/call` with name `{server_name}__{tool_name}`: strips the prefix, routes to the corresponding backend via `mcp_proxy.proxy_mcp`.
- On `resources/list`, `resources/read`, `prompts/list`, `prompts/get`: same namespace-and-route pattern.
- On `initialize`: returns merged capabilities (intersection of what all backends support, since the agent can only rely on what's universally available).

Per-tenant tool catalog cache (~5min TTL) to avoid fanning out `tools/list` to every backend on every aggregator request. Invalidate on registry change.

Aggregator naming: **prefix by default with `{server_name}__`, allow dashboard rename for ergonomic display**. The rename is just an alias — the underlying call still uses the prefix internally.

`Authorization` handling at the aggregator: a `tools/call` is a single JSON-RPC request that routes to exactly one backend, so the agent's `Authorization` header is forwarded to that backend like in the egress-proxy case. `tools/list` is the only method that fans out to N backends with potentially N different credentials — for v1 the aggregator serves a **cached tool catalog** populated by successful per-backend `tools/list` calls (either from earlier agent traffic to `/mcp/{server_name}` or from an explicit one-off probe the operator runs from the dashboard). The aggregator does not invent any new request header conventions; agent-side code is unchanged from talking to a single MCP server.

### 7. Per-agent attribution

No new code needed — reuse the passive fingerprinting that already attributes chat traffic per the `per-agent-traffic-metering` memory: user-agent, source IP, API-key hash, request shape. The MCP proxy calls the existing `identify_agent(request)` helper before policy/DLP/signing.

One nuance: MCP clients tend to have different user-agent shapes than LLM SDKs (`mcp-python/1.4.0` vs `openai-python/1.50.0`). The existing fingerprint rules may need a small update to capture the MCP-side rich user-agent strings. Validate during testing in milestone M4 below.

## Files to touch

Backend:
- `src/kyde/migrations/NNNN_mcp_routing.sql` — `mcp_servers`, `mcp_tool_policies` tables
- `src/kyde/mcp_proxy.py` *(new)*
- `src/kyde/mcp_registry.py` *(new)*
- `src/kyde/mcp_aggregator.py` *(new)*
- `src/kyde/dlp_json_walk.py` *(new)*
- `src/kyde/server.py` — mount `/mcp/` route prefix
- `src/kyde/dashboard.py` — registry admin endpoints
- `src/kyde/signing.py` — `McpLedgerEntry` support, canonicalization
- `src/kyde/dlp.py` — expose `_scan_regex` / `_scan_bert` for reuse by JSON walker
- `src/kyde/ledger.py` — `kind` column on entries table, insert path for MCP entries

Frontend:
- `frontend/src/api/types.ts` — `McpServer`, `McpTool`, `McpToolPolicy`
- `frontend/src/api/queries.ts` — registry CRUD hooks, tool list hook, policy update hook
- `frontend/src/pages/mcp-servers.tsx` *(new)* — list view
- `frontend/src/pages/mcp-server-detail.tsx` *(new)* — per-server config, tool list, policy matrix
- `frontend/src/pages/dlp-triage.tsx` — add MCP source filter chip

Tests:
- `tests/test_mcp_proxy.py` — JSON-RPC parsing, routing, policy enforcement, `Authorization` header pass-through
- `tests/test_mcp_registry.py` — CRUD, cache invalidation
- `tests/test_dlp_json_walk.py` — finding extraction across nested payloads
- `tests/test_mcp_aggregator.py` — namespacing, fanout, cached `tools/list`
- `tests/fixtures/mcp/` — recorded fixtures from Notion / GitHub / a bearer-token server for integration tests

## Milestones / rollout

**M0 — Foundation (week 1)** — ✅ DONE
Schema migration in. `mcp_proxy.py` skeleton with JSON-RPC parsing, `Authorization` header pass-through, but no DLP/signing/policy yet. Manually-inserted registry row pointing at a public test MCP server. End-to-end: a curl with a bearer token hits `/mcp/test`, gets forwarded to the upstream with the same `Authorization` header, response returned. **Validation:** request flows through, ledger has *no* entry yet, log line proves the parse + forward worked and headers were preserved.

**M1 — First provider end-to-end (week 2)** — ✅ DONE
Dashboard "Add MCP Server" form (`name` + `upstream_url`). Smoke-tested against httpbin (not Notion specifically — same JSON-RPC envelope shape, no OAuth dance needed for the build). Operator obtains an upstream access token via the provider's own OAuth flow (in their own tooling, not in Kyde), pastes it into curl / their agent → agent calls `/mcp/<name>` with `Authorization: Bearer <token>` → request reaches upstream via the gateway → tool list visible in UI (the dashboard does a cached `tools/list` populated lazily from agent traffic, or via an operator-triggered "probe" that asks the operator for a one-off token used only for that request and not stored). **Validation:** CRUD via `/api/mcp/servers` + agent call via `:4000/mcp/<name>` round-trips clean, with Authorization passed through and X-Agent-ID stripped. *Real-provider validation against Notion still recommended before declaring v1 done.*

**M2 — Signing + DLP (week 3)** — 🔲 NEXT
`McpLedgerEntry` shipped. `dlp_json_walk.py` shipped with extractors for `tools/call` and `resources/read`. Every Notion call produces a signed ledger entry. DLP findings appear in the triage UI with the new source filter. **Validation:** trigger a DLP rule from Notion page content; confirm finding + signed ledger entry; verify signature offline.

**M3 — Per-tool policy (week 4)** — 🔲 OPEN
`mcp_tool_policies` table populated via dashboard matrix. Deny rules enforced in `mcp_proxy.check_policy`. Denied calls return JSON-RPC error with `outcome="blocked"` ledger entry. **Validation:** set `notion-delete-page` to deny for an agent; confirm call is blocked; confirm ledger entry recorded.

**M4 — Aggregator + second provider (week 5)** — 🔲 OPEN
`mcp_aggregator.py` shipped with cached `tools/list`. Add GitHub MCP server alongside Notion. Agent points at `/mcp/` (aggregator), sees both `notion__search` and `github__search_repositories` in `tools/list` (catalog cache), can call both with the appropriate `Authorization` header per call. **Validation:** single agent successfully calls tools across two backends through the aggregator, with separate credentials per call.

**M5 — Production hardening (week 6)** — 🔲 OPEN
Production concerns: rate limiting per server, retry/circuit-breaker on upstream failures, audit-logging of registry CRUD operations, upstream-error surfacing in the dashboard ("last call to `notion` returned 401 — check the credential your agent is sending"). Third real provider for diversity: pick one that uses a non-OAuth credential (e.g., a self-hosted bearer-token MCP server or a Stripe-style API key). **Validation:** load test of 100 concurrent tool calls across three providers, no errors, all entries signed, DLP applied to all.

**Launch gate:** All M0–M5 validations pass, integration tests against Notion + GitHub + the bearer-token server green, dashboard usability tested with at least one external operator.

## Validation against real providers

Pin to specific versions and record fixtures so tests don't drift:

For each provider, the test rig obtains a working upstream token out-of-band (via that provider's normal OAuth flow, IDE plugin, or API-key issuance — exactly as a real customer would) and the test agent sends it in `Authorization`. The gateway forwards it; nothing about the credential is stored or refreshed by Kyde.

| Provider | MCP spec | Agent-supplied credential | Test scope |
|---|---|---|---|
| **Notion** | 2025-09-03 API, mcp-spec 2025-06-18 | Bearer (operator obtains via Notion OAuth in their own tooling) | `tools/list`, `notion-search`, `notion-fetch`, `notion-create-pages` |
| **GitHub** | latest official remote | Bearer (operator generates a GitHub installation/PAT token) | `tools/list`, a read tool, a write tool (to exercise per-tool deny) |
| **One bearer-token server** | self-hosted dev MCP | Static bearer | sanity check; also exercises the long-lived-token path |

Out-of-band: do a one-off compat check against Atlassian post their SSE sunset (June 30), against Google BigQuery's MCP server, and against Microsoft Azure DevOps once we have a tenant who wants them. Not v1 launch blockers.

## Risks

- **MCP spec churn.** Expect 1–2 breaking spec revisions per year. Mitigation: isolate spec-version-sensitive code behind a thin `mcp_protocol.py` module; pin the version we implement in a constant; surface "MCP spec version" in the dashboard so customers can see what we support.
- **Upstream auth failures look like gateway failures to customers.** When the agent sends a stale Notion token and gets a 401 back through the gateway, the customer may blame Kyde. Mitigation: surface the upstream status code and a snippet of the response body in the dashboard's "last error" field per server; document clearly in onboarding that credential lifecycle is the agent layer's responsibility; in the JSON-RPC error envelope returned to the agent, include `kyde_pass_through: true` so frameworks can distinguish.
- **Aggregator `tools/list` cache staleness.** A new tool added to a backend won't appear in the aggregated catalog until the cache is invalidated. Mitigation: short TTL (5 min) plus explicit "refresh catalog" action in the dashboard; on a `tools/call` to an unknown tool, the proxy falls back to a real-time `tools/list` against the implicated backend.
- **Result-size blowup.** A `read_file` MCP call against a 50MB file is one JSON-RPC response. Buffering for DLP scales linearly with payload size. Mitigation: enforce a per-call max-response-size limit (configurable, default 10MB); log + alert when hit; v1.5 ships streaming DLP if a customer needs larger payloads.
- **Aggregator initialize semantics.** When backends have different MCP capabilities, the aggregator must return the intersection. Edge cases (one backend supports subscriptions, another doesn't) may break agents that assume capability A is universal. Mitigation: document the aggregator's capability-intersection rule; add a per-agent option to use a single-backend endpoint (`/mcp/{server_name}`) instead of the aggregator when capability matters.
- **DLP cost on hot tools.** The generic JSON walker is slower than the chat-specific extractors. Some MCP tools will be called thousands of times per day. Mitigation: profile after M2; add per-tool extractors for the top 5 hot tools per tenant if needed; consider a "DLP off for this tool" config knob (with audit-logged justification) for cases where the operator confirms the tool returns no sensitive data.

## Effort

Rough estimate: **3–4 engineer-weeks** for the milestone sequence above, assuming one engineer focused on this and the existing chat-routing/signing/DLP infrastructure stays stable. The credential-out-of-scope decision removes ~2–3 weeks of OAuth-adapter, token-vault, refresh-handling, and provider-specific UI work that the original draft carried. Add ~1 week for the dashboard polish needed to make the registry/policy UI demoable. Realistically: **4–5 weeks calendar** with normal interrupt level.

Compressible further to ~2.5 weeks if we cut the aggregator (workstream 6) from v1 and ship egress-proxy-only, but the aggregator is the differentiating product feature against "just point your agent at the MCP server directly" — strongly recommend keeping it in v1.

## Open questions to resolve before starting

1. **MCP spec version pin.** Which spec revision do we target for v1? Recommend the latest stable as of M0 start. Lock in `mcp_protocol.py`.
2. **Aggregator vs egress proxy default.** Does the agent typically get pointed at `/mcp/` (aggregator) or `/mcp/{server}` (direct)? Recommend aggregator as default for the unified tool catalog story; allow direct as opt-out.
3. **Aggregator `tools/list` cache seeding.** Cold-start, the catalog is empty. Options: (a) lazy-populate from first agent traffic per backend, (b) operator clicks "probe" in the dashboard and supplies a one-off credential used only for that single `tools/list` call and not stored, (c) document that the agent must hit each `/mcp/{server}` once before the aggregated `tools/list` is meaningful. Recommend (a) + (b) together; never (c) alone — empty catalog is a bad first-run experience.
