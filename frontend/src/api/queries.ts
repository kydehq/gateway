import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { fetchJSON, qs } from "./client";
import type {
  Agent,
  AgentTrafficRow,
  PathKind,
  TrafficMode,
  Configuration,
  CreateUserResponse,
  DlpAlert,
  DlpAlertEvent,
  FleetTrust,
  HostLabelRow,
  HostResolveResponse,
  HostStatusFilter,
  EntriesPage,
  EntryDetail,
  EntryFacets,
  Me,
  ResetPasswordResponse,
  ServiceMetrics,
  SessionDetail,
  SessionsPage,
  CreateDlpRuleInput,
  DlpHealth,
  DlpRule,
  ReapplyAllowlistResult,
  SettingEntry,
  SmtpTestResult,
  Stats,
  TokenAnalysis,
  VerificationRun,
  TopologyAgent,
  TopologyFlow,
  TopologyIp,
  TopologyResponse,
  TopologySegment,
  TopologyWindow,
  TpmStatus,
  TransitionInput,
  User,
  Verify,
  McpServer,
  McpServersResponse,
  McpToolPolicy,
  McpToolPoliciesResponse,
  McpAggregatorCatalog,
  AdminAction,
  AdminActionsResponse,
  Policy,
  PolicyListResponse,
  PolicyResyncResponse,
} from "./types";

// Query-key factory — one source of truth for cache keys. All the
// mutation invalidations below reference these. Pattern: `[domain, ...args]`.
export const qk = {
  me: ["me"] as const,
  stats: (window: StatsWindow) => ["stats", window] as const,
  verify: ["verify"] as const,
  tokenAnalysis: (window: StatsWindow, agentId?: string) =>
    ["token-analysis", window, agentId ?? null] as const,
  fleetTrust: (window: StatsWindow) => ["fleet-trust", window] as const,
  // Base prefix used for cache invalidation; the parameterised variant
  // below scopes a single read by source filter without changing how
  // mutations invalidate (which always blow away every variant).
  dlpAlerts: ["dlp-alerts"] as const,
  dlpAlertsFiltered: (sourceType?: "chat" | "mcp" | null) =>
    ["dlp-alerts", sourceType ?? "all"] as const,
  tpmStatus: ["tpm-status"] as const,
  entryFacets: ["entry-facets"] as const,
  entries: (params: TimelineParams) => ["entries", params] as const,
  entry: (ref: string | null) => ["entry", ref] as const,
  sessions: (filters: SessionFilters) => ["sessions", filters] as const,
  session: (id: string | null) => ["session", id] as const,
  users: (includeDeleted: boolean) => ["users", includeDeleted] as const,
  configuration: ["configuration"] as const,
  serviceMetrics: ["service-metrics"] as const,
  settings: ["settings"] as const,
  dlpRules: ["dlp-rules"] as const,
  dlpHealth: ["dlp-health"] as const,
  dlpAlertEvents: (alertId: string | null) => ["dlp-alert-events", alertId] as const,
  agents: ["agents"] as const,
  agentTraffic: (agentId: string | null) => ["agent-traffic", agentId] as const,
  hostResolve: (identifier: string | null) => ["host-resolve", identifier] as const,
  hostLabels: (status: string, q: string) => ["host-labels", status, q] as const,
  verificationRuns: (limit: number) => ["verification-runs", limit] as const,
  topology: (window: TopologyWindow, minValue: number) =>
    ["topology", window, minValue] as const,
  topologyFlow: (
    sourceLayer: string | null,
    sourceLabel: string | null,
    targetLayer: string | null,
    targetLabel: string | null,
    window: TopologyWindow,
  ) =>
    ["topology-flow", sourceLayer, sourceLabel, targetLayer, targetLabel, window] as const,
  topologySegment: (subnet: string | null, window: TopologyWindow) =>
    ["topology-segment", subnet, window] as const,
  topologyAgent: (agentId: string | null, window: TopologyWindow) =>
    ["topology-agent", agentId, window] as const,
  topologyIp: (ip: string | null, window: TopologyWindow) =>
    ["topology-ip", ip, window] as const,
  mcpServers: ["mcp-servers"] as const,
  mcpPolicies: (serverName: string) =>
    ["mcp-policies", serverName] as const,
  mcpAggregatorCatalog: ["mcp-aggregator-catalog"] as const,
  adminActions: (
    actorId: number | null,
    action: string | null,
    resourceType: string | null,
    limit: number,
    offset: number,
  ) =>
    ["admin-actions", actorId, action, resourceType, limit, offset] as const,
  policies: ["policies"] as const,
};

export interface TimelineParams {
  action?: string;
  upstream?: string;
  q?: string;
  window?: StatsWindow;
  agent_id?: string;
  session_id?: string;
}

// Window options shared by /api/stats and /api/token-analysis. Topology has
// its own narrower set (no 90d, no all) because Sankey charting struggles
// with very wide windows.
export type StatsWindow = "1h" | "24h" | "7d" | "30d" | "90d" | "all";
export const STATS_WINDOWS: StatsWindow[] = ["1h", "24h", "7d", "30d", "90d", "all"];
export const DEFAULT_STATS_WINDOW: StatsWindow = "24h";

export type SessionSort = "newest" | "oldest" | "entries" | "agents";
export const SESSION_SORTS: SessionSort[] = ["newest", "oldest", "entries", "agents"];

export type HasAlertFilter = "any" | "yes" | "no";

// SessionStatus is defined in api/types.ts; re-exported here so consumers
// pulling filter helpers from queries.ts don't need to import from both.
export type { SessionStatus } from "./types";
import type { SessionStatus as _SS } from "./types";
export const SESSION_STATUSES: _SS[] = ["blocked", "observed", "allowed"];

export interface SessionFilters {
  window: StatsWindow;
  has_alert: HasAlertFilter;
  agents: string[];
  sort: SessionSort;
  status: _SS[];
}

export const DEFAULT_SESSION_FILTERS: SessionFilters = {
  window: "24h",
  has_alert: "any",
  agents: [],
  sort: "newest",
  status: [],
};

const PAGE_SIZE = 50;

export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => fetchJSON<Me>("/api/whoami"),
  });
}

// Auto-refresh cadence for dashboards. Low enough to feel live, high
// enough to stay well under noticeable server load. Can be overridden
// per page if needed.
const REFRESH_MS = 30_000;

export function useStats(window: StatsWindow = DEFAULT_STATS_WINDOW) {
  return useQuery({
    queryKey: qk.stats(window),
    queryFn: () => fetchJSON<Stats>(`/api/stats?window=${window}`),
    refetchInterval: REFRESH_MS,
  });
}

export function useFleetTrust(window: StatsWindow = DEFAULT_STATS_WINDOW) {
  return useQuery({
    queryKey: qk.fleetTrust(window),
    queryFn: () => fetchJSON<FleetTrust>(`/api/fleet-trust?window=${window}`),
    refetchInterval: REFRESH_MS,
  });
}

export function useVerify() {
  return useQuery({
    queryKey: qk.verify,
    queryFn: () => fetchJSON<Verify>("/api/verify"),
    refetchInterval: REFRESH_MS,
  });
}

export function useTokenAnalysis(
  window: StatsWindow = DEFAULT_STATS_WINDOW,
  agentId?: string,
) {
  return useQuery({
    queryKey: qk.tokenAnalysis(window, agentId),
    queryFn: () =>
      fetchJSON<TokenAnalysis>(
        `/api/token-analysis?window=${window}` +
          (agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""),
      ),
    refetchInterval: REFRESH_MS,
  });
}

export function useDlpAlerts(sourceType?: "chat" | "mcp" | null) {
  const qs = sourceType
    ? `?source_type=${encodeURIComponent(sourceType)}`
    : "";
  return useQuery({
    queryKey: qk.dlpAlertsFiltered(sourceType),
    queryFn: () => fetchJSON<DlpAlert[]>(`/api/dlp-alerts${qs}`),
    refetchInterval: REFRESH_MS,
  });
}

// Single-alert fetch — used when another page (e.g. agent-chains) needs
// to open the detail sheet for a specific alert without paying for the
// full list. Skips when `alertId` is null so callers can call it
// unconditionally and gate it via state.
export function useDlpAlert(alertId: string | null) {
  return useQuery({
    queryKey: ["dlp-alert", alertId] as const,
    enabled: !!alertId,
    queryFn: () =>
      fetchJSON<DlpAlert>(`/api/dlp-alerts/${encodeURIComponent(alertId!)}`),
  });
}

// Host resolution — Phase 2.
//
// useHostResolve drives the /hosts/:identifier page when the identifier
// is a hostname; the IP branch goes through useTopologyIp instead. We
// keep this hook narrow rather than rolling it into useTopologyIp so
// the host page can disable the call when the identifier is clearly an
// IP and avoid an extra round-trip.
export function useHostResolve(identifier: string | null) {
  return useQuery({
    queryKey: qk.hostResolve(identifier),
    queryFn: () =>
      fetchJSON<HostResolveResponse>(
        "/api/hosts/resolve?identifier=" + encodeURIComponent(identifier!),
      ),
    enabled: !!identifier,
  });
}

export function useHostLabels(status: HostStatusFilter = "all", q = "") {
  return useQuery({
    queryKey: qk.hostLabels(status, q),
    queryFn: () =>
      fetchJSON<HostLabelRow[]>(
        "/api/host-labels" + qs({ status, q: q || undefined }),
      ),
    refetchInterval: REFRESH_MS,
  });
}

export function useUpsertHostLabel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ip, hostname }: { ip: string; hostname: string }) =>
      fetchJSON<{ ip: string; hostname: string; source: "admin" }>(
        "/api/host-labels/" + encodeURIComponent(ip),
        { method: "PUT", body: JSON.stringify({ hostname }) },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["host-labels"] }),
  });
}

export function useDeleteHostLabel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      fetchJSON<{ ip: string; cleared: true }>(
        "/api/host-labels/" + encodeURIComponent(ip),
        { method: "DELETE" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["host-labels"] }),
  });
}

export function useRefreshHostLabel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) =>
      fetchJSON<{ ip: string; hostname: string | null; source: string }>(
        "/api/host-labels/" + encodeURIComponent(ip) + "/refresh",
        { method: "POST" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["host-labels"] }),
  });
}

export function useAgents() {
  return useQuery({
    queryKey: qk.agents,
    queryFn: () => fetchJSON<Agent[]>("/api/agents"),
    refetchInterval: REFRESH_MS,
  });
}

export function useVerificationRuns(limit = 30) {
  return useQuery({
    queryKey: qk.verificationRuns(limit),
    queryFn: () => fetchJSON<VerificationRun[]>(`/api/verification-runs?limit=${limit}`),
    refetchInterval: REFRESH_MS,
  });
}

export function useBlockAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ agent_id, reason }: { agent_id: string; reason?: string }) =>
      fetchJSON<{ agent_id: string; blocked_at: number }>(
        "/api/agents/" + encodeURIComponent(agent_id) + "/block",
        { method: "POST", body: JSON.stringify({ reason: reason ?? "" }) },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.agents }),
  });
}

export function useUnblockAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (agent_id: string) =>
      fetchJSON<void>(
        "/api/agents/" + encodeURIComponent(agent_id) + "/block",
        { method: "DELETE" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.agents }),
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ agent_id, display_name }: { agent_id: string; display_name: string | null }) =>
      fetchJSON<{ agent_id: string; display_name: string | null }>(
        "/api/agents/" + encodeURIComponent(agent_id),
        { method: "PATCH", body: JSON.stringify({ display_name }) },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.agents }),
  });
}

export function useAgentTraffic(agentId: string | null) {
  return useQuery({
    queryKey: qk.agentTraffic(agentId),
    queryFn: () =>
      fetchJSON<{ items: AgentTrafficRow[] }>(
        agentId
          ? "/api/agent-traffic?agent_id=" + encodeURIComponent(agentId)
          : "/api/agent-traffic",
      ),
    enabled: true,
    select: (data) => data.items,
  });
}

export function useSetTrafficMode(agentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ path_kind, mode }: { path_kind: PathKind; mode: TrafficMode }) =>
      fetchJSON<{ agent_id: string; path_kind: string; mode: TrafficMode }>(
        "/api/agent-traffic/" +
          encodeURIComponent(agentId) +
          "/" +
          encodeURIComponent(path_kind) +
          "/mode",
        { method: "POST", body: JSON.stringify({ mode }) },
      ),
    onSuccess: () => {
      // Refresh both the agent-scoped view and the cross-agent index so
      // a flip is visible everywhere immediately.
      qc.invalidateQueries({ queryKey: qk.agentTraffic(agentId) });
      qc.invalidateQueries({ queryKey: qk.agentTraffic(null) });
    },
  });
}

export function useTpmStatus() {
  return useQuery({
    queryKey: qk.tpmStatus,
    queryFn: () => fetchJSON<TpmStatus>("/api/tpm-status"),
  });
}

export function useConfiguration() {
  return useQuery({
    queryKey: qk.configuration,
    queryFn: () => fetchJSON<Configuration>("/api/configuration"),
    refetchInterval: REFRESH_MS,
  });
}

export function useServiceMetrics() {
  return useQuery({
    queryKey: qk.serviceMetrics,
    queryFn: () => fetchJSON<ServiceMetrics>("/api/metrics"),
    refetchInterval: REFRESH_MS,
  });
}

export function useEntryFacets() {
  return useQuery({
    queryKey: qk.entryFacets,
    queryFn: () => fetchJSON<EntryFacets>("/api/entries/facets"),
  });
}

export function useEntriesInfinite(params: TimelineParams) {
  return useInfiniteQuery({
    queryKey: qk.entries(params),
    queryFn: ({ pageParam, signal }) =>
      fetchJSON<EntriesPage>(
        "/api/entries" +
          qs({
            limit: PAGE_SIZE,
            cursor: pageParam || undefined,
            action: params.action,
            upstream: params.upstream,
            agent_id: params.agent_id,
            session_id: params.session_id,
            q: params.q,
            window: params.window ?? DEFAULT_STATS_WINDOW,
          }),
        { signal },
      ),
    initialPageParam: "" as string,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor ?? undefined : undefined),
  });
}

export function useEntry(
  ref: string | null,
  options?: Partial<UseQueryOptions<EntryDetail>>,
) {
  return useQuery({
    queryKey: qk.entry(ref),
    queryFn: () => fetchJSON<EntryDetail>("/api/entry/" + encodeURIComponent(ref!)),
    enabled: !!ref,
    ...options,
  });
}

export function useSessionsInfinite(filters: SessionFilters = DEFAULT_SESSION_FILTERS) {
  return useInfiniteQuery({
    queryKey: qk.sessions(filters),
    queryFn: ({ pageParam, signal }) => {
      // `agent` is a repeated query param; qs() helper takes scalar values
      // so we build the agents portion manually.
      const base = qs({
        limit: PAGE_SIZE,
        cursor: pageParam || undefined,
        window: filters.window,
        has_alert: filters.has_alert !== "any" ? filters.has_alert : undefined,
        sort: filters.sort,
      });
      const agentQs = filters.agents
        .map((a) => "agent=" + encodeURIComponent(a))
        .join("&");
      const statusQs = filters.status
        .map((s) => "status=" + encodeURIComponent(s))
        .join("&");
      const repeated = [agentQs, statusQs].filter(Boolean).join("&");
      const url =
        "/api/sessions" +
        (repeated ? (base ? base + "&" + repeated : "?" + repeated) : base);
      return fetchJSON<SessionsPage>(url, { signal });
    },
    initialPageParam: "" as string,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor ?? undefined : undefined),
  });
}

export function useSession(id: string | null) {
  return useQuery({
    queryKey: qk.session(id),
    queryFn: () => fetchJSON<SessionDetail>("/api/sessions/" + encodeURIComponent(id!)),
    enabled: !!id,
  });
}

export function useUsers(includeDeleted: boolean) {
  return useQuery({
    queryKey: qk.users(includeDeleted),
    // Backend declares `include_deleted: int`, so we must send 0/1 rather
    // than the JS-friendly "true"/"false". Sending "false" yields a 422.
    queryFn: () =>
      fetchJSON<User[]>("/api/users" + (includeDeleted ? "?include_deleted=1" : "")),
  });
}

// ─── Mutations ─────────────────────────────────────────────────────────

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; email?: string; password?: string; roles: string[] }) =>
      fetchJSON<CreateUserResponse>("/api/users", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    // Backend handler is PATCH /api/users/{id} (dashboard.py). Using PUT
    // here previously hit a 405.
    mutationFn: ({ id, ...body }: { id: string | number; email?: string; roles?: string[]; enabled?: boolean }) =>
      fetchJSON<void>("/api/users/" + encodeURIComponent(String(id)), {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string | number) =>
      fetchJSON<void>("/api/users/" + encodeURIComponent(String(id)), { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useResetUserPassword() {
  return useMutation({
    mutationFn: (id: string | number) =>
      fetchJSON<ResetPasswordResponse>(
        "/api/users/" + encodeURIComponent(String(id)) + "/reset-password",
        { method: "POST" },
      ),
  });
}

export function useUnlockUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string | number) =>
      fetchJSON<void>("/api/users/" + encodeURIComponent(String(id)) + "/unlock", {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string }) =>
      fetchJSON<void>("/api/profile/email", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.me }),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      fetchJSON<void>("/api/profile/password", { method: "POST", body: JSON.stringify(body) }),
  });
}

// ─── Runtime settings (admin) ──────────────────────────────────────────

export function useSettings() {
  return useQuery({
    queryKey: qk.settings,
    queryFn: () => fetchJSON<SettingEntry[]>("/api/settings"),
    refetchInterval: REFRESH_MS,
  });
}

export function useUpdateSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      fetchJSON<void>("/api/settings/" + encodeURIComponent(key), {
        method: "PATCH",
        body: JSON.stringify({ value }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}

export function useResetSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      fetchJSON<void>("/api/settings/" + encodeURIComponent(key), { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}

// Sends a canned test email through the currently-saved SMTP config to
// every user with the `auditor` role. Does NOT touch dlp_alerts.
export function useSendTestEmail() {
  return useMutation({
    mutationFn: () =>
      fetchJSON<SmtpTestResult>("/api/settings/smtp/test", { method: "POST" }),
  });
}

// ─── DLP allow/block rules (admin) ────────────────────────────────────

export function useDlpRules() {
  return useQuery({
    queryKey: qk.dlpRules,
    queryFn: () => fetchJSON<DlpRule[]>("/api/dlp-rules"),
    refetchInterval: REFRESH_MS,
  });
}

// Live health of the built-in DLP sidecars (BERT + regex). Used by the
// compliance page to evidence "DLP scanning is active" — the scanners
// are always-on with preloaded rules; the only thing the UI checks is
// whether the gateway can still reach them.
export function useDlpHealth() {
  return useQuery({
    queryKey: qk.dlpHealth,
    queryFn: () => fetchJSON<DlpHealth>("/api/dlp/health"),
    refetchInterval: REFRESH_MS,
  });
}

export function useCreateDlpRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateDlpRuleInput) =>
      fetchJSON<DlpRule>("/api/dlp-rules", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.dlpRules }),
  });
}

export function useDeleteDlpRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      fetchJSON<{ ok: boolean; id: number }>("/api/dlp-rules/" + id, {
        method: "DELETE",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.dlpRules }),
  });
}

// ─── DLP alert triage ────────────────────────────────────────────────

export function useDlpAlertEvents(alertId: string | null) {
  return useQuery({
    queryKey: qk.dlpAlertEvents(alertId),
    queryFn: () =>
      fetchJSON<DlpAlertEvent[]>(
        "/api/dlp-alerts/" + encodeURIComponent(alertId!) + "/events",
      ),
    enabled: !!alertId,
  });
}

// Drives every SOC triage action: claim, start, pending_info, escalate,
// close (with disposition), reopen. Server enforces the state machine —
// invalid transitions come back as 400/403 and are surfaced as toasts.
export function useTransitionDlpAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ alert_id, ...body }: TransitionInput) =>
      fetchJSON<DlpAlert>(
        "/api/dlp-alerts/" + encodeURIComponent(alert_id) + "/transition",
        { method: "POST", body: JSON.stringify(body) },
      ),
    onSuccess: (_row, vars) => {
      qc.invalidateQueries({ queryKey: qk.dlpAlerts });
      qc.invalidateQueries({ queryKey: qk.dlpAlertEvents(vars.alert_id) });
    },
  });
}

// Retroactively apply the current allowlist across all OPEN alerts.
// Fully-suppressed alerts flip to status='allowlisted'; partials are
// left in place (see backend dlp.reapply_allowlist_to_open_alerts).
export function useReapplyAllowlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetchJSON<ReapplyAllowlistResult>("/api/dlp-rules/reapply", {
        method: "POST",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.dlpAlerts });
      qc.invalidateQueries({ queryKey: qk.dlpRules });
    },
  });
}

// ─── Agent topology (Sankey) ─────────────────────────────────────────

export function useTopology(window: TopologyWindow, minValue: number = 1) {
  return useQuery({
    queryKey: qk.topology(window, minValue),
    queryFn: () =>
      fetchJSON<TopologyResponse>(
        `/api/topology?window=${window}&min_value=${minValue}`,
      ),
    refetchInterval: REFRESH_MS,
  });
}

export function useTopologyFlow(
  source: { layer: string; label: string } | null,
  target: { layer: string; label: string } | null,
  window: TopologyWindow,
) {
  // Disabled until the user clicks a link — both endpoints must be set.
  const enabled = source !== null && target !== null;
  return useQuery({
    queryKey: qk.topologyFlow(
      source?.layer ?? null,
      source?.label ?? null,
      target?.layer ?? null,
      target?.label ?? null,
      window,
    ),
    queryFn: () =>
      fetchJSON<TopologyFlow>(
        "/api/topology/flow" +
          `?source_layer=${encodeURIComponent(source!.layer)}` +
          `&source_label=${encodeURIComponent(source!.label)}` +
          `&target_layer=${encodeURIComponent(target!.layer)}` +
          `&target_label=${encodeURIComponent(target!.label)}` +
          `&window=${window}`,
      ),
    enabled,
  });
}

export function useTopologySegment(
  subnet: string | null,
  window: TopologyWindow,
) {
  return useQuery({
    queryKey: qk.topologySegment(subnet, window),
    queryFn: () =>
      // FastAPI path param accepts slashes raw, but encoding is still safe.
      fetchJSON<TopologySegment>(
        `/api/topology/segment/${subnet}?window=${window}`,
      ),
    enabled: !!subnet,
  });
}

export function useTopologyAgent(
  agentId: string | null,
  window: TopologyWindow,
) {
  return useQuery({
    queryKey: qk.topologyAgent(agentId, window),
    queryFn: () =>
      fetchJSON<TopologyAgent>(
        `/api/topology/agent/${encodeURIComponent(agentId!)}?window=${window}`,
      ),
    enabled: !!agentId,
  });
}

export function useTopologyIp(ip: string | null, window: TopologyWindow) {
  return useQuery({
    queryKey: qk.topologyIp(ip, window),
    queryFn: () =>
      fetchJSON<TopologyIp>(
        `/api/topology/ip/${encodeURIComponent(ip!)}?window=${window}`,
      ),
    enabled: !!ip,
  });
}

// ─── MCP servers ────────────────────────────────────────────────────────

export function useMcpServers() {
  return useQuery({
    queryKey: qk.mcpServers,
    queryFn: async () => {
      const r = await fetchJSON<McpServersResponse>("/api/mcp/servers");
      return r.items;
    },
  });
}

export function useCreateMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; upstream_url: string; enabled?: boolean }) =>
      fetchJSON<McpServer>("/api/mcp/servers", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.mcpServers }),
  });
}

export function useUpdateMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      ...body
    }: {
      name: string;
      upstream_url?: string;
      enabled?: boolean;
    }) =>
      fetchJSON<McpServer>("/api/mcp/servers/" + encodeURIComponent(name), {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.mcpServers }),
  });
}

export function useDeleteMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      fetchJSON<void>("/api/mcp/servers/" + encodeURIComponent(name), {
        method: "DELETE",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.mcpServers }),
  });
}

// ─── MCP per-tool policies ─────────────────────────────────────────────
// Each (server, agent_id, tool_name) row carries a decision and optional
// reason. agent_id="*" and tool_name="*" are literal wildcards; the
// proxy applies most-specific-wins precedence. Empty list ⇒ default allow.

export function useMcpPolicies(serverName: string, enabled = true) {
  return useQuery({
    queryKey: qk.mcpPolicies(serverName),
    queryFn: async () => {
      const r = await fetchJSON<McpToolPoliciesResponse>(
        `/api/mcp/servers/${encodeURIComponent(serverName)}/policies`,
      );
      return r.items;
    },
    enabled: enabled && !!serverName,
  });
}

export function useSetMcpPolicy(serverName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      agent_id: string;
      tool_name: string;
      decision: "allow" | "deny";
      reason?: string | null;
    }) =>
      fetchJSON<McpToolPolicy>(
        `/api/mcp/servers/${encodeURIComponent(serverName)}/policies/` +
          `${encodeURIComponent(body.agent_id)}/${encodeURIComponent(body.tool_name)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            decision: body.decision,
            reason: body.reason ?? null,
          }),
        },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.mcpPolicies(serverName) }),
  });
}

export function useDeleteMcpPolicy(serverName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { agent_id: string; tool_name: string }) =>
      fetchJSON<void>(
        `/api/mcp/servers/${encodeURIComponent(serverName)}/policies/` +
          `${encodeURIComponent(body.agent_id)}/${encodeURIComponent(body.tool_name)}`,
        { method: "DELETE" },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.mcpPolicies(serverName) }),
  });
}

// ─── MCP aggregator catalog ────────────────────────────────────────────
// Namespaced union of every enabled server's tools, seeded opportunistically
// from real /tools/list traffic and probe-tools runs. Read-only — operators
// refresh by hitting the per-server probe button.

export function useMcpAggregatorCatalog() {
  return useQuery({
    queryKey: qk.mcpAggregatorCatalog,
    queryFn: () =>
      fetchJSON<McpAggregatorCatalog>("/api/mcp/aggregator/catalog"),
  });
}

// ─── Admin action audit log ────────────────────────────────────────────
// Operational telemetry for "who changed what, when" across MCP servers,
// MCP tool policies and DLP policies. Separate from the signed ledger
// chain; backed by /api/audit-log (admin-only).

export interface AdminActionsParams {
  actor_id?: number | null;
  action?: string | null;
  resource_type?: string | null;
  limit?: number;
  offset?: number;
}

export function useAdminActions(params: AdminActionsParams = {}) {
  const limit = params.limit ?? 100;
  const offset = params.offset ?? 0;
  const actorId = params.actor_id ?? null;
  const action = params.action ?? null;
  const resourceType = params.resource_type ?? null;
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (actorId != null) qs.set("actor_id", String(actorId));
  if (action) qs.set("action", action);
  if (resourceType) qs.set("resource_type", resourceType);
  return useQuery({
    queryKey: qk.adminActions(actorId, action, resourceType, limit, offset),
    queryFn: () =>
      fetchJSON<AdminActionsResponse>(`/api/audit-log?${qs.toString()}`),
  });
}

// Re-export the row type so consumers can import from one place.
export type { AdminAction };

// ─── DLP policies ───────────────────────────────────────────────────────

export function usePolicies() {
  return useQuery({
    queryKey: qk.policies,
    queryFn: async () => {
      const r = await fetchJSON<PolicyListResponse>("/api/dlp-policies");
      return r.items;
    },
  });
}

export function useTogglePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      enabled,
      prevention,
    }: {
      id: string;
      enabled?: boolean;
      prevention?: boolean;
    }) =>
      fetchJSON<Policy>("/api/dlp-policies/" + encodeURIComponent(id), {
        method: "PATCH",
        body: JSON.stringify({
          ...(enabled !== undefined ? { enabled } : {}),
          ...(prevention !== undefined ? { prevention } : {}),
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.policies });
      qc.invalidateQueries({ queryKey: qk.dlpAlerts });
    },
  });
}

export function usePreventionBulk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      fetchJSON<{ updated: number }>("/api/dlp-policies/prevention-bulk", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.policies }),
  });
}

export function useResyncPolicies() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetchJSON<PolicyResyncResponse>("/api/dlp-policies/resync", {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.policies }),
  });
}
