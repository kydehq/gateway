import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock only fetchJSON; qs() stays real so hooks exercise genuine URL
// serialization and the assertions below verify the exact wire format.
const fetchJSON = vi.hoisted(() => vi.fn());
vi.mock("./client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./client")>()),
  fetchJSON,
}));

import * as q from "./queries";
import { qk } from "./queries";

// ─── Harness ────────────────────────────────────────────────────────────

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, invalidate, wrapper };
}

// The hooks are individually typed; the runners only need this shape.
interface QueryLike {
  data: unknown;
  isSuccess: boolean;
  isError: boolean;
  fetchStatus: string;
}
interface MutationLike {
  mutate: (vars?: unknown) => void;
  isSuccess: boolean;
  data: unknown;
}

beforeEach(() => {
  fetchJSON.mockReset();
});

/** Query hook resolves and hit exactly `url`. */
function itFetches(
  name: string,
  hook: () => unknown,
  url: string,
  opts?: { response?: unknown; expectData?: unknown },
) {
  it(`${name} fetches ${url}`, async () => {
    const response = opts?.response ?? { marker: name };
    fetchJSON.mockResolvedValue(response);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(hook, { wrapper });
    await waitFor(() =>
      expect((result.current as QueryLike).isSuccess).toBe(true),
    );
    expect(fetchJSON).toHaveBeenCalledWith(url);
    expect((result.current as QueryLike).data).toEqual(
      "expectData" in (opts ?? {}) ? opts!.expectData : response,
    );
  });
}

/** Query hook with enabled:false never fires a request. */
function itDisabled(name: string, hook: () => unknown) {
  it(`${name} stays idle when disabled`, async () => {
    fetchJSON.mockResolvedValue({});
    const { wrapper } = makeWrapper();
    const { result } = renderHook(hook, { wrapper });
    expect((result.current as QueryLike).fetchStatus).toBe("idle");
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchJSON).not.toHaveBeenCalled();
  });
}

/** Mutation hook sends the expected request and invalidates the expected keys. */
function itMutates(
  name: string,
  hook: () => unknown,
  spec: {
    vars?: unknown;
    url: string;
    init?: Record<string, unknown>;
    invalidated?: ReadonlyArray<readonly unknown[]>;
    response?: unknown;
  },
) {
  it(`${name} calls ${spec.url}`, async () => {
    fetchJSON.mockResolvedValue(spec.response ?? { ok: true });
    const { wrapper, invalidate } = makeWrapper();
    const { result } = renderHook(hook, { wrapper });
    act(() => (result.current as MutationLike).mutate(spec.vars));
    await waitFor(() =>
      expect((result.current as MutationLike).isSuccess).toBe(true),
    );
    if (spec.init) {
      expect(fetchJSON).toHaveBeenCalledWith(spec.url, spec.init);
    } else {
      expect(fetchJSON).toHaveBeenCalledWith(spec.url, expect.anything());
    }
    for (const key of spec.invalidated ?? []) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: key });
    }
  });
}

// ─── Plain queries ──────────────────────────────────────────────────────

describe("query hooks", () => {
  itFetches("useMe", () => q.useMe(), "/api/whoami");
  itFetches("useStats (default window)", () => q.useStats(), "/api/stats?window=24h");
  itFetches("useStats (7d)", () => q.useStats("7d"), "/api/stats?window=7d");
  itFetches("useFleetTrust", () => q.useFleetTrust("1h"), "/api/fleet-trust?window=1h");
  itFetches("useVerify", () => q.useVerify(), "/api/verify");
  itFetches(
    "useTokenAnalysis (no agent)",
    () => q.useTokenAnalysis(),
    "/api/token-analysis?window=24h",
  );
  itFetches(
    "useTokenAnalysis (agent id is encoded)",
    () => q.useTokenAnalysis("7d", "agent:a b"),
    "/api/token-analysis?window=7d&agent_id=agent%3Aa%20b",
  );
  itFetches("useDlpAlerts (all)", () => q.useDlpAlerts(), "/api/dlp-alerts");
  itFetches(
    "useDlpAlerts (mcp only)",
    () => q.useDlpAlerts("mcp"),
    "/api/dlp-alerts?source_type=mcp",
  );
  itFetches(
    "useDlpAlert",
    () => q.useDlpAlert("a/1"),
    "/api/dlp-alerts/a%2F1",
  );
  itDisabled("useDlpAlert(null)", () => q.useDlpAlert(null));
  itFetches(
    "useHostResolve",
    () => q.useHostResolve("web-01"),
    "/api/hosts/resolve?identifier=web-01",
  );
  itDisabled("useHostResolve(null)", () => q.useHostResolve(null));
  itFetches(
    "useHostLabels (defaults)",
    () => q.useHostLabels(),
    "/api/host-labels?status=all",
  );
  itFetches(
    "useHostLabels (filtered)",
    () => q.useHostLabels("unlabeled", "db"),
    "/api/host-labels?status=unlabeled&q=db",
  );
  itFetches("useAgents", () => q.useAgents(), "/api/agents");
  itFetches(
    "useVerificationRuns (default limit)",
    () => q.useVerificationRuns(),
    "/api/verification-runs?limit=30",
  );
  itFetches(
    "useVerificationRuns (custom limit)",
    () => q.useVerificationRuns(5),
    "/api/verification-runs?limit=5",
  );
  itFetches(
    "useAgentTraffic (fleet-wide) unwraps items",
    () => q.useAgentTraffic(null),
    "/api/agent-traffic",
    { response: { items: [{ path_kind: "chat" }] }, expectData: [{ path_kind: "chat" }] },
  );
  itFetches(
    "useAgentTraffic (agent-scoped)",
    () => q.useAgentTraffic("agent:a b"),
    "/api/agent-traffic?agent_id=agent%3Aa%20b",
    { response: { items: [] }, expectData: [] },
  );
  itFetches("useTpmStatus", () => q.useTpmStatus(), "/api/tpm-status");
  itFetches("useConfiguration", () => q.useConfiguration(), "/api/configuration");
  itFetches("useServiceMetrics", () => q.useServiceMetrics(), "/api/metrics");
  itFetches("useEntryFacets", () => q.useEntryFacets(), "/api/entries/facets");
  itFetches("useEntry", () => q.useEntry("e 1"), "/api/entry/e%201");
  itDisabled("useEntry(null)", () => q.useEntry(null));
  itFetches("useSession", () => q.useSession("s1"), "/api/sessions/s1");
  itDisabled("useSession(null)", () => q.useSession(null));
  itFetches("useUsers (active only)", () => q.useUsers(false), "/api/users");
  itFetches(
    "useUsers (include deleted → 0/1 flag)",
    () => q.useUsers(true),
    "/api/users?include_deleted=1",
  );
  itFetches("useSettings", () => q.useSettings(), "/api/settings");
  itFetches("useDlpRules", () => q.useDlpRules(), "/api/dlp-rules");
  itFetches("useDlpHealth", () => q.useDlpHealth(), "/api/dlp/health");
  itFetches(
    "useDlpAlertEvents",
    () => q.useDlpAlertEvents("a1"),
    "/api/dlp-alerts/a1/events",
  );
  itDisabled("useDlpAlertEvents(null)", () => q.useDlpAlertEvents(null));
  itFetches(
    "useTopology (default min)",
    () => q.useTopology("24h"),
    "/api/topology?window=24h&min_value=1",
  );
  itFetches(
    "useTopologyFlow",
    () =>
      q.useTopologyFlow(
        { layer: "agents", label: "a b" },
        { layer: "upstreams", label: "openai" },
        "24h",
      ),
    "/api/topology/flow?source_layer=agents&source_label=a%20b&target_layer=upstreams&target_label=openai&window=24h",
  );
  itDisabled("useTopologyFlow (no endpoints)", () =>
    q.useTopologyFlow(null, null, "24h"),
  );
  itFetches(
    "useTopologySegment",
    () => q.useTopologySegment("10.0.0.0/24", "24h"),
    "/api/topology/segment/10.0.0.0/24?window=24h",
  );
  itDisabled("useTopologySegment(null)", () => q.useTopologySegment(null, "24h"));
  itFetches(
    "useTopologyAgent",
    () => q.useTopologyAgent("agent:x", "1h"),
    "/api/topology/agent/agent%3Ax?window=1h",
  );
  itDisabled("useTopologyAgent(null)", () => q.useTopologyAgent(null, "1h"));
  itFetches(
    "useTopologyIp",
    () => q.useTopologyIp("10.0.0.5", "24h"),
    "/api/topology/ip/10.0.0.5?window=24h",
  );
  itDisabled("useTopologyIp(null)", () => q.useTopologyIp(null, "24h"));
  itFetches(
    "useMcpServers unwraps items",
    () => q.useMcpServers(),
    "/api/mcp/servers",
    { response: { items: [{ name: "srv" }] }, expectData: [{ name: "srv" }] },
  );
  itFetches(
    "useMcpPolicies unwraps items",
    () => q.useMcpPolicies("my srv"),
    "/api/mcp/servers/my%20srv/policies",
    { response: { items: [] }, expectData: [] },
  );
  itDisabled("useMcpPolicies (explicitly disabled)", () =>
    q.useMcpPolicies("srv", false),
  );
  itDisabled("useMcpPolicies (empty server name)", () => q.useMcpPolicies(""));
  itFetches(
    "useMcpAggregatorCatalog",
    () => q.useMcpAggregatorCatalog(),
    "/api/mcp/aggregator/catalog",
  );
  itFetches(
    "useAdminActions (defaults)",
    () => q.useAdminActions(),
    "/api/audit-log?limit=100&offset=0",
  );
  itFetches(
    "useAdminActions (all filters)",
    () =>
      q.useAdminActions({
        actor_id: 7,
        action: "mcp_server.create",
        resource_type: "mcp_server",
        limit: 10,
        offset: 20,
      }),
    "/api/audit-log?limit=10&offset=20&actor_id=7&action=mcp_server.create&resource_type=mcp_server",
  );
  itFetches(
    "usePolicies unwraps items",
    () => q.usePolicies(),
    "/api/dlp-policies",
    { response: { items: [{ id: "p1" }] }, expectData: [{ id: "p1" }] },
  );

  it("surfaces fetch errors through the query result", async () => {
    fetchJSON.mockRejectedValue(new Error("boom"));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => q.useMe(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("boom");
  });
});

// ─── Infinite queries ───────────────────────────────────────────────────

describe("useEntriesInfinite", () => {
  it("serializes filters and pages via next_cursor", async () => {
    fetchJSON
      .mockResolvedValueOnce({ items: [{ n: 1 }], has_more: true, next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [{ n: 2 }], has_more: false, next_cursor: null });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => q.useEntriesInfinite({ q: "ssh", window: "7d" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchJSON).toHaveBeenCalledWith(
      "/api/entries?limit=50&q=ssh&window=7d",
      { signal: expect.any(AbortSignal) },
    );
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(fetchJSON).toHaveBeenLastCalledWith(
      "/api/entries?limit=50&cursor=c1&q=ssh&window=7d",
      { signal: expect.any(AbortSignal) },
    );
    expect(result.current.hasNextPage).toBe(false);
  });

  it("falls back to the default window when none is given", async () => {
    fetchJSON.mockResolvedValue({ items: [], has_more: false, next_cursor: null });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => q.useEntriesInfinite({}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchJSON).toHaveBeenCalledWith("/api/entries?limit=50&window=24h", {
      signal: expect.any(AbortSignal),
    });
  });
});

describe("useSessionsInfinite", () => {
  it("uses the default filters", async () => {
    fetchJSON.mockResolvedValue({ items: [], has_more: false, next_cursor: null });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => q.useSessionsInfinite(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchJSON).toHaveBeenCalledWith(
      "/api/sessions?limit=50&window=24h&sort=newest",
      { signal: expect.any(AbortSignal) },
    );
  });

  it("appends repeated agent/status params after the scalar ones", async () => {
    fetchJSON.mockResolvedValue({ items: [], has_more: false, next_cursor: null });
    const filters: q.SessionFilters = {
      window: "7d",
      has_alert: "yes",
      agents: ["agent:a b", "agent:c"],
      sort: "entries",
      status: ["blocked"],
    };
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => q.useSessionsInfinite(filters), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchJSON).toHaveBeenCalledWith(
      "/api/sessions?limit=50&window=7d&has_alert=yes&sort=entries" +
        "&agent=agent%3Aa%20b&agent=agent%3Ac&status=blocked",
      { signal: expect.any(AbortSignal) },
    );
  });
});

// ─── Mutations ──────────────────────────────────────────────────────────

describe("mutation hooks", () => {
  itMutates("useUpsertHostLabel", () => q.useUpsertHostLabel(), {
    vars: { ip: "10.0.0.5", hostname: "db-1" },
    url: "/api/host-labels/10.0.0.5",
    init: { method: "PUT", body: JSON.stringify({ hostname: "db-1" }) },
    invalidated: [["host-labels"]],
  });
  itMutates("useDeleteHostLabel", () => q.useDeleteHostLabel(), {
    vars: "10.0.0.5",
    url: "/api/host-labels/10.0.0.5",
    init: { method: "DELETE" },
    invalidated: [["host-labels"]],
  });
  itMutates("useRefreshHostLabel", () => q.useRefreshHostLabel(), {
    vars: "10.0.0.5",
    url: "/api/host-labels/10.0.0.5/refresh",
    init: { method: "POST" },
    invalidated: [["host-labels"]],
  });
  itMutates("useBlockAgent (encodes id, defaults reason)", () => q.useBlockAgent(), {
    vars: { agent_id: "agent:a b" },
    url: "/api/agents/agent%3Aa%20b/block",
    init: { method: "POST", body: JSON.stringify({ reason: "" }) },
    invalidated: [qk.agents],
  });
  itMutates("useUnblockAgent", () => q.useUnblockAgent(), {
    vars: "agent:a",
    url: "/api/agents/agent%3Aa/block",
    init: { method: "DELETE" },
    invalidated: [qk.agents],
  });
  itMutates("useUpdateAgent", () => q.useUpdateAgent(), {
    vars: { agent_id: "agent:a", display_name: "Build Bot" },
    url: "/api/agents/agent%3Aa",
    init: { method: "PATCH", body: JSON.stringify({ display_name: "Build Bot" }) },
    invalidated: [qk.agents],
  });
  itMutates(
    "useSetTrafficMode (refreshes agent + fleet views)",
    () => q.useSetTrafficMode("agent:a"),
    {
      vars: { path_kind: "chat", mode: "full" },
      url: "/api/agent-traffic/agent%3Aa/chat/mode",
      init: { method: "POST", body: JSON.stringify({ mode: "full" }) },
      invalidated: [qk.agentTraffic("agent:a"), qk.agentTraffic(null)],
    },
  );
  itMutates("useCreateUser", () => q.useCreateUser(), {
    vars: { username: "kim", roles: ["viewer"] },
    url: "/api/users",
    init: {
      method: "POST",
      body: JSON.stringify({ username: "kim", roles: ["viewer"] }),
    },
    invalidated: [["users"]],
  });
  itMutates("useUpdateUser (PATCH, id stripped from body)", () => q.useUpdateUser(), {
    vars: { id: 7, email: "kim@example.com", roles: ["admin"] },
    url: "/api/users/7",
    init: {
      method: "PATCH",
      body: JSON.stringify({ email: "kim@example.com", roles: ["admin"] }),
    },
    invalidated: [["users"]],
  });
  itMutates("useDeleteUser", () => q.useDeleteUser(), {
    vars: 7,
    url: "/api/users/7",
    init: { method: "DELETE" },
    invalidated: [["users"]],
  });
  itMutates("useResetUserPassword", () => q.useResetUserPassword(), {
    vars: 7,
    url: "/api/users/7/reset-password",
    init: { method: "POST" },
  });
  itMutates("useUnlockUser", () => q.useUnlockUser(), {
    vars: 7,
    url: "/api/users/7/unlock",
    init: { method: "POST" },
    invalidated: [["users"]],
  });
  itMutates("useUpdateEmail (invalidates me)", () => q.useUpdateEmail(), {
    vars: { email: "me@example.com" },
    url: "/api/profile/email",
    init: { method: "POST", body: JSON.stringify({ email: "me@example.com" }) },
    invalidated: [qk.me],
  });
  itMutates("useChangePassword", () => q.useChangePassword(), {
    vars: { current_password: "old", new_password: "new" },
    url: "/api/profile/password",
    init: {
      method: "POST",
      body: JSON.stringify({ current_password: "old", new_password: "new" }),
    },
  });
  itMutates("useUpdateSetting", () => q.useUpdateSetting(), {
    vars: { key: "smtp.host", value: "mail.local" },
    url: "/api/settings/smtp.host",
    init: { method: "PATCH", body: JSON.stringify({ value: "mail.local" }) },
    invalidated: [qk.settings],
  });
  itMutates("useResetSetting", () => q.useResetSetting(), {
    vars: "smtp.host",
    url: "/api/settings/smtp.host",
    init: { method: "DELETE" },
    invalidated: [qk.settings],
  });
  itMutates("useSendTestEmail", () => q.useSendTestEmail(), {
    url: "/api/settings/smtp/test",
    init: { method: "POST" },
  });
  itMutates("useCreateDlpRule", () => q.useCreateDlpRule(), {
    vars: { kind: "allow", pattern: "test-*" },
    url: "/api/dlp-rules",
    init: {
      method: "POST",
      body: JSON.stringify({ kind: "allow", pattern: "test-*" }),
    },
    invalidated: [qk.dlpRules],
  });
  itMutates("useDeleteDlpRule", () => q.useDeleteDlpRule(), {
    vars: 3,
    url: "/api/dlp-rules/3",
    init: { method: "DELETE" },
    invalidated: [qk.dlpRules],
  });
  itMutates(
    "useTransitionDlpAlert (invalidates list + event log)",
    () => q.useTransitionDlpAlert(),
    {
      vars: { alert_id: "a1", action: "claim" },
      url: "/api/dlp-alerts/a1/transition",
      init: { method: "POST", body: JSON.stringify({ action: "claim" }) },
      invalidated: [qk.dlpAlerts, qk.dlpAlertEvents("a1")],
    },
  );
  itMutates(
    "useReapplyAllowlist (invalidates alerts + rules)",
    () => q.useReapplyAllowlist(),
    {
      url: "/api/dlp-rules/reapply",
      init: { method: "POST" },
      invalidated: [qk.dlpAlerts, qk.dlpRules],
    },
  );
  itMutates("useCreateMcpServer", () => q.useCreateMcpServer(), {
    vars: { name: "srv", upstream_url: "http://mcp:9000" },
    url: "/api/mcp/servers",
    init: {
      method: "POST",
      body: JSON.stringify({ name: "srv", upstream_url: "http://mcp:9000" }),
    },
    invalidated: [qk.mcpServers],
  });
  itMutates("useUpdateMcpServer (name stripped from body)", () => q.useUpdateMcpServer(), {
    vars: { name: "my srv", enabled: false },
    url: "/api/mcp/servers/my%20srv",
    init: { method: "PATCH", body: JSON.stringify({ enabled: false }) },
    invalidated: [qk.mcpServers],
  });
  itMutates("useDeleteMcpServer", () => q.useDeleteMcpServer(), {
    vars: "my srv",
    url: "/api/mcp/servers/my%20srv",
    init: { method: "DELETE" },
    invalidated: [qk.mcpServers],
  });
  itMutates("useSetMcpPolicy (defaults reason to null)", () => q.useSetMcpPolicy("srv"), {
    vars: { agent_id: "*", tool_name: "read file", decision: "deny" },
    url: "/api/mcp/servers/srv/policies/*/read%20file",
    init: {
      method: "PUT",
      body: JSON.stringify({ decision: "deny", reason: null }),
    },
    invalidated: [qk.mcpPolicies("srv")],
  });
  itMutates("useDeleteMcpPolicy", () => q.useDeleteMcpPolicy("srv"), {
    vars: { agent_id: "*", tool_name: "read file" },
    url: "/api/mcp/servers/srv/policies/*/read%20file",
    init: { method: "DELETE" },
    invalidated: [qk.mcpPolicies("srv")],
  });
  itMutates(
    "useTogglePolicy (sends only the provided flags)",
    () => q.useTogglePolicy(),
    {
      vars: { id: "p1", prevention: true },
      url: "/api/dlp-policies/p1",
      init: { method: "PATCH", body: JSON.stringify({ prevention: true }) },
      invalidated: [qk.policies, qk.dlpAlerts],
    },
  );
  itMutates("usePreventionBulk", () => q.usePreventionBulk(), {
    vars: true,
    url: "/api/dlp-policies/prevention-bulk",
    init: { method: "POST", body: JSON.stringify({ enabled: true }) },
    invalidated: [qk.policies],
  });
  itMutates("useResyncPolicies", () => q.useResyncPolicies(), {
    url: "/api/dlp-policies/resync",
    init: { method: "POST" },
    invalidated: [qk.policies],
  });

  it("useTogglePolicy sends both flags when both are provided", async () => {
    fetchJSON.mockResolvedValue({ ok: true });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => q.useTogglePolicy(), { wrapper });
    act(() => result.current.mutate({ id: "p1", enabled: false, prevention: true }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchJSON).toHaveBeenCalledWith("/api/dlp-policies/p1", {
      method: "PATCH",
      body: JSON.stringify({ enabled: false, prevention: true }),
    });
  });
});
