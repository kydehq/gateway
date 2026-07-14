# MCP Configuration & Routing — v1 Implementation Plan

## Context

`docs/plans/mcp-routing-v1.md` covers the gateway-internal work: proxy, registry, DLP, signing, policy, aggregator. That plan ends at "operator pastes a URL and the proxy works."

This plan covers the surface that operators and agent developers actually touch:

1. **Which MCP services we explicitly support at launch**, with the URL, credential shape, and known quirks for each one captured as a catalog the dashboard can render.
2. **How agents are pointed at the gateway** — concrete configuration snippets for the common agent frameworks (Cursor, Claude Desktop, Continue, LangChain/LlamaIndex MCP clients, generic HTTP MCP clients).
3. **What the dashboard "Add MCP Server" flow looks like** when you have a catalog instead of a blank URL field.
4. **The first-customer docs** so that early design partners can wire everything up without reading source code.

This is the **operator UX + integration docs** side of MCP routing. It depends on workstreams 1–2 of `mcp-routing-v1.md` being in place but does not depend on aggregator (workstream 6) or DLP (workstream 3) for its own validation.

## Scope

In scope:

- Service catalog as a data structure (JSON), shipped in the repo, surfaced in the dashboard.
- Per-service template entries: upstream URL, recommended display name, link to provider's credential-issuance docs, agent-supplied credential shape, known tool subset (informational).
- Dashboard "Add MCP Server" wizard with a template picker plus a "custom URL" path.
- Per-server detail page renders a **configuration snippet** showing the agent-side URL and a placeholder for the `Authorization` header, copy-pasteable.
- Agent-framework integration docs for the five clients that account for nearly all real MCP usage in 2026.

Out of scope (explicit deferrals):

- **Auto-discovery of MCP servers** from the catalog (e.g., crawling each tenant's GitHub / Notion accounts to find MCP-eligible apps). Operator picks manually.
- **Bundled credential issuance** (e.g., "Kyde will help you generate a GitHub PAT"). That's the agent layer's job, per the credential-handling-out-of-scope stance.
- **Per-service tool taxonomy** (mapping each provider's tools into a normalized capability schema). v2.
- **Catalog distribution over the control plane.** v1 ships catalog as static JSON in the gateway image; control-plane-pushed catalog is v1.5 if catalogue churn becomes painful.

## Service catalog v1

Two tiers. Tier 1 services ship as **templates in the dashboard wizard** with a quickstart guide. Tier 2 services are **known-compatible with the custom URL path** and listed in a "tested servers" reference doc but don't get their own UI template at v1.

### Tier 1 — first-class templates (5 services)

Selected for: realistic demo coverage, diverse credential shapes, and well-documented public MCP endpoints as of May 2026.

| Service | Upstream URL (canonical, verify before shipping) | Credential the agent sends | Why in Tier 1 |
|---|---|---|---|
| **Notion** | `https://mcp.notion.com/mcp` | OAuth bearer (workspace-scoped) | Cleanest MCP rollout in the ecosystem; clear docs; covers the OAuth-bearer agent path. |
| **GitHub** | `https://api.githubcopilot.com/mcp` *(GitHub-managed remote)* | OAuth bearer (installation token or fine-grained PAT) | Most-requested by developer-facing customers; exercises read/write tools for per-tool deny demo. |
| **Linear** | `https://mcp.linear.app/mcp` | OAuth bearer | Smallest blast radius for first non-Notion OAuth; well-documented. |
| **Slack** | provider-documented endpoint *(verify at impl time — Slack's MCP host changed twice in 2026)* | OAuth bearer (workspace) | Customer messaging surface; high enterprise demand. |
| **Self-hosted bearer-token MCP server** | operator-supplied | Static bearer in dashboard config helper text *(reminder, not storage)* | Covers the long-lived-token agent path used by CI jobs / batch workflows. |

**Note on the URLs:** every entry above must be re-verified against the provider's current docs at the M1 implementation moment — provider-hosted MCP URLs have churned through 2026 and a stale entry in the catalog is worse than no entry. The plan does not pin them.

### Tier 2 — known-compatible, custom-URL path (no template UI)

These are documented in a single reference page (`docs/guides/mcp-tested-servers.md`, to be created) with the URL, credential shape, and a one-line "tested on YYYY-MM-DD" note. No dashboard template, no quickstart of their own; operators use the custom-URL flow.

- Atlassian (Jira, Confluence) — OAuth bearer
- HubSpot — OAuth bearer
- Salesforce — OAuth bearer
- Stripe — API key (Stripe restricted key, sent as bearer)
- Shopify — OAuth bearer
- Cloudflare (per-product MCP servers, ~13 of them) — API token
- Vercel — OAuth bearer
- Sentry — OAuth bearer or DSN
- Google managed MCP servers (`*.googleapis.com/mcp`) — OAuth bearer or ADC-derived bearer
- Microsoft Azure DevOps (`mcp.dev.azure.com`) — Entra-issued bearer

Promotion criterion for moving a Tier 2 service into Tier 1: a paying customer explicitly asks, OR the service shows up in three or more onboarding requests.

## Agent-side routing

The agent points at the gateway instead of the upstream MCP server directly. From the agent's perspective the URL just changes; the `Authorization` header is unchanged (the same credential the agent would have sent to the upstream is now sent to the gateway and forwarded as-is).

### Gateway URL shape

```
http(s)://{gateway-host}:{port}/mcp/{server_name}   ← direct, single backend
http(s)://{gateway-host}:{port}/mcp/                ← aggregator, fans out
```

- `{gateway-host}` — a VPC-local hostname the customer's DNS resolves (e.g., `kyde-gateway.internal`).
- `{port}` — the gateway's MCP listener. v1 reuses port 4000 (existing chat-proxy port) with a new `/mcp/` route prefix; future option is a dedicated port.
- `{server_name}` — the routing handle the operator chose when registering the upstream in the dashboard (e.g., `notion`, `github`, `acme-internal-tools`).

The dashboard's per-server detail page **renders this URL for the operator to copy into agent configuration**, including a worked example for whichever framework the operator selects from a dropdown.

### Integration snippets

These are the snippets the dashboard renders and the integration docs include. Each shows "before" (agent pointed at the upstream) and "after" (agent pointed at the gateway) — the only change is the URL; the credential the agent sends is unchanged.

**Cursor / Claude Desktop / any client using the `mcpServers` JSON config format:**

```json
{
  "mcpServers": {
    "notion": {
      "url": "https://kyde-gateway.internal:4000/mcp/notion",
      "headers": {
        "Authorization": "Bearer <your-notion-token>"
      }
    }
  }
}
```

**Continue / Cline / similar IDE plugins** — same JSON shape, same field names (URL plus optional headers map).

**LangChain MCP client (`langchain-mcp-adapters`):**

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "notion": {
        "url": "https://kyde-gateway.internal:4000/mcp/notion",
        "transport": "streamable_http",
        "headers": {"Authorization": f"Bearer {notion_token}"},
    },
})
```

**LlamaIndex MCP integration** — equivalent pattern: pass URL plus headers; the rest of the agent code is unchanged.

**Generic HTTP MCP client / custom agent code:**

```python
# Whatever HTTP client you use, change the base URL from the upstream to the gateway.
# Authorization header is whatever the upstream wanted (Bearer, basic, etc.) — pass through.
import httpx
resp = await httpx.post(
    "https://kyde-gateway.internal:4000/mcp/notion",
    headers={"Authorization": f"Bearer {notion_token}"},
    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
)
```

**Aggregator endpoint** — same shape, drop the `{server_name}` from the URL:

```json
{
  "mcpServers": {
    "kyde": {
      "url": "https://kyde-gateway.internal:4000/mcp/",
      "headers": {
        "Authorization": "Bearer <per-call-token-or-omitted>"
      }
    }
  }
}
```

The aggregator's `Authorization` story is per-call: each `tools/call` carries the bearer for the backend the prefix routes to. For agents that send a single header for all aggregator traffic, that header is forwarded to whichever backend matches; collisions (one bearer not valid for backend B) surface as upstream 401s in the dashboard's per-server "last error" field.

## Dashboard "Add MCP Server" flow

The wizard becomes a template picker on top of the bare `{name, upstream_url}` form from `mcp-routing-v1.md` workstream 2.

```
Step 1: Pick a service
   ┌────────────────────────────────────────┐
   │  Notion                                │  ← Tier 1 template
   │  GitHub                                │
   │  Linear                                │
   │  Slack                                 │
   │  Self-hosted bearer server             │
   │  ─────────────────────────────────     │
   │  Custom (enter URL manually)           │  ← Tier 2 + anything else
   └────────────────────────────────────────┘

Step 2: Confirm details
   - Display name:   notion        ← pre-filled from template, editable
   - Upstream URL:   https://...   ← pre-filled from template, editable
   - Documentation: [link to provider's credential-issuance docs]
   - Reminder: "You will configure the credential your agent sends in your agent's
     own configuration. Kyde does not store credentials."

Step 3: Success page
   Shows the gateway URL the operator gives their agent:
     https://kyde-gateway.internal:4000/mcp/notion
   With copy-paste configuration snippets for each supported framework (tabs).
```

The wizard never asks for a credential. The success page links to the per-framework integration guides.

The "Custom" path skips Step 1 and goes straight to a bare URL field with the same Step 3.

## Per-server detail page additions

On top of the per-agent policy matrix from `mcp-routing-v1.md` workstream 4, the per-server detail page renders:

- **Agent configuration snippet panel** — same content as the wizard's Step 3, regenerated against the current `{server_name, upstream_url}` so renames stay accurate.
- **Last error** — surface the upstream's most recent non-2xx response (HTTP status code + first 200 chars of body, redacted of headers) so the operator can tell whether a failure was Kyde's fault or the credential the agent is sending. Per the upstream-failures risk in `mcp-routing-v1.md`.
- **Provider docs link** — for template-derived servers, link to the provider's MCP page; for custom servers, blank.

## Files & deliverables

Backend:
- `src/kyde/mcp_catalog.py` *(new)* — loads the catalog from `mcp_catalog.json`; exposes `list_templates()`, `get_template(slug)`. No DB; static data.
- `src/kyde/mcp_catalog.json` *(new)* — the Tier 1 template entries.
- `src/kyde/dashboard.py` — new endpoint `GET /api/mcp/catalog` returning the template list.

Frontend:
- `frontend/src/pages/mcp-add.tsx` *(new)* — the template-picker wizard.
- `frontend/src/components/mcp-config-snippet.tsx` *(new)* — renders the per-framework copy-paste snippets, used by both the wizard success page and the per-server detail page. Driven by `{server_name, upstream_url, gateway_host}` props.
- `frontend/src/pages/mcp-server-detail.tsx` — add the snippet panel and last-error panel.
- `frontend/src/api/queries.ts` — `useMcpCatalog`.

Docs (new directory `docs/guides/`):
- `docs/guides/mcp-integration.md` — single-page integration guide with framework tabs (Cursor, Claude Desktop, Continue, LangChain, LlamaIndex, generic).
- `docs/guides/mcp-tested-servers.md` — Tier 2 reference page (one entry per service).

Tests:
- `tests/test_mcp_catalog.py` — schema validation: every entry has the required fields, every Tier 1 URL is reachable in a weekly CI ping (informational; failure doesn't block PRs, but opens a ticket).
- `tests/test_mcp_config_snippet.tsx` — snapshot tests on the generated snippets for each framework × a representative server.

## Milestones

**C0 — Catalog plumbing (3 days)**
`mcp_catalog.json` schema defined and frozen (slug, display_name, upstream_url, docs_url, credential_hint, default_server_name). `mcp_catalog.py` + `/api/mcp/catalog` endpoint shipped. Empty catalog except a single dev placeholder. Frontend reads it. **Validation:** template dropdown renders in the wizard with the placeholder entry.

**C1 — Tier 1 templates + integration guide (1 week)**
Five Tier 1 entries populated (URLs re-verified against current provider docs at this moment, not at plan-write time). `docs/guides/mcp-integration.md` published with framework tabs. `mcp-config-snippet.tsx` component shipped and rendered on the wizard success page and the per-server detail page. **Validation:** one external user (an early design partner, or an internal dogfooder unfamiliar with the project) follows the guide and successfully gets Notion working through the gateway from Cursor or Claude Desktop without asking for help. Screen recording captured.

**C2 — Tier 2 reference page (2 days, can parallelize)**
`docs/guides/mcp-tested-servers.md` populated with the Tier 2 list. Each entry has been smoke-tested through the custom-URL path at least once; "tested on YYYY-MM-DD" stamps included. **Validation:** the page exists and every URL listed has a verifying smoke test commit in the repo.

**C3 — Polish & launch readiness (3 days)**
Last-error panel on per-server detail page (per `mcp-routing-v1.md` risk mitigation). Wizard "Step 3" success page tested for copy-paste correctness on all five frameworks (no stray quotes, valid JSON). Catalog freshness CI job added (weekly probe of Tier 1 upstream URLs; opens a tracking issue on failure). **Validation:** launch checklist completes — all C0–C2 validations pass, no known stale URLs, all snippets copy-paste-clean.

**Launch gate:** C0–C3 done.

## Validation

Per-template manual smoke test (one engineer, runs at C1 closeout and again at launch):

| Template | Smoke test |
|---|---|
| Notion | Operator adds via wizard; agent (Cursor) calls `notion-search` through `/mcp/notion`; result returns; ledger entry visible; DLP applied. |
| GitHub | Same flow; one read tool call (e.g., `list_repositories`); one denied write tool (per-tool policy demo). |
| Linear | Same flow; `list_issues` call. |
| Slack | Same flow; `list_channels` (read) call. |
| Self-hosted bearer | Standup a local test MCP server; operator adds via wizard; agent (LangChain) calls it. |

## Risks

- **Provider URL churn.** The Tier 1 URLs above can change between this plan being written and C1 starting. Mitigation: re-verify every URL at the start of C1; do not freeze them in the plan; weekly probe in CI after launch (C4).
- **Provider docs churn for credential issuance.** The "where do I get this token" links in the catalog rot fast. Mitigation: link to the provider's *root* developer-docs page rather than a deep link, accept that the operator does one extra click to navigate.
- **Framework configuration format churn.** Cursor's JSON config format has shifted between major versions. Mitigation: snippet templates carry a "tested with Cursor vX.Y" footer; revisit annually or on user reports.
- **Customer expects credential storage.** First-time operators may assume the wizard will store their token. Mitigation: Step 2 of the wizard has explicit "Kyde does not store credentials — configure this in your agent" language, and the success page reinforces it.
- **Aggregator's `Authorization` story confuses operators.** "Why does the aggregator endpoint use the same single header but the dashboard says credentials are per-backend?" Mitigation: integration guide has a dedicated "Aggregator vs direct endpoint" section with worked examples for both.

## Effort

Rough estimate: **2 engineer-weeks** for one engineer, sequenced as C0 (3d) → C1 (1w) → C2 (2d, parallel) → C3 (3d). Calendar: **2–3 weeks**.

## Open questions

1. **Catalog source of truth.** Static JSON in the gateway image (proposed) vs. control-plane-served (lets us push URL fixes without a gateway redeploy). Recommend static for v1; revisit after one round of URL churn.
2. **Where the integration guide lives.** Same repo as the gateway (proposed, `docs/guides/`) vs. a separate marketing/docs site. Repo for v1 keeps it close to the code; move to a docs site once we have one.
3. **Per-framework tab count.** Five frameworks proposed (Cursor, Claude Desktop, Continue, LangChain, LlamaIndex, plus generic). Should we add CrewAI, AutoGen, OpenAI Assistants? Recommend: launch with the five, add others on customer ask.
4. **Self-hosted bearer template — does it ship?** It's not a "service" with a provider URL, it's a deploy pattern. Recommend keeping it in Tier 1 because customers building internal MCP servers are a real audience and the wizard flow validates with no third party involved.

## Related

- `docs/plans/mcp-routing-v1.md` — gateway-internal MCP work this builds on.
