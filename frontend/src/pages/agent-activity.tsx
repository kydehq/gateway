import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  PieChart,
  Pie,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from "recharts";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ChartCard } from "@/components/shared/chart-card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  STATS_WINDOWS,
  useAgents,
  useBlockAgent,
  useDlpAlerts,
  useStats,
  useTokenAnalysis,
  useTopologyAgent,
  type StatsWindow,
} from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMe } from "@/hooks/use-me";
import { useFeatures } from "@/hooks/use-features";
import { PaidLock } from "@/components/shared/upgrade-lock";
import { toast } from "sonner";
import { fmtTokens, truncate } from "@/lib/format";
import { useAgentLabel } from "@/hooks/use-agent-label";
import { ACTION_TYPE_LABEL, type ActionType } from "@/lib/action-types";
import { cn } from "@/lib/utils";

const GRID_STROKE = "hsl(var(--chart-grid))";
const TICK_STYLE = { fill: "hsl(var(--chart-axis))", fontSize: 11, fontFamily: "var(--font-mono)" };
const TOOLTIP_STYLE = {
  background: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 6,
  fontSize: 12,
  fontFamily: "var(--font-mono)",
};
const CHART_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

function computeBaseline(values: number[]) {
  if (!values.length) return { mean: 0, stddev: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / values.length;
  return { mean, stddev: Math.sqrt(variance) };
}

function rewriteProvider(raw: string): string {
  if (!raw || raw === "(none)") return "Direct (no provider)";
  if (raw.includes("anthropic")) return "Anthropic";
  if (raw.includes("openai")) return "OpenAI";
  if (raw.includes("google")) return "Google";
  if (raw.includes("azure")) return "Azure OpenAI";
  if (raw.includes("bedrock")) return "AWS Bedrock";
  return raw;
}


type Metric = "tokens" | "calls";

const WINDOW_LABEL: Record<StatsWindow, string> = {
  "1h": "Last 1 hour",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  all: "All time",
};

// Convert a StatsWindow to a UTC cutoff timestamp (ms). Used to filter
// the unwindowed /api/dlp-alerts feed down to the page's selected window
// so the alert KPI tracks the same period as the rest of the cards.
function windowCutoffMs(window: StatsWindow): number {
  const HOUR = 60 * 60 * 1000;
  const DAY = 24 * HOUR;
  switch (window) {
    case "1h":  return Date.now() - HOUR;
    case "24h": return Date.now() - 24 * HOUR;
    case "7d":  return Date.now() - 7 * DAY;
    case "30d": return Date.now() - 30 * DAY;
    case "90d": return Date.now() - 90 * DAY;
    case "all": return 0;
  }
}

const WINDOW_SHORT: Record<StatsWindow, string> = {
  "1h": "1h", "24h": "24h", "7d": "7d", "30d": "30d", "90d": "90d", all: "all time",
};

export default function AgentActivityPage() {
  const [window, setWindow] = useState<StatsWindow>("30d");
  const { data: stats, isLoading: statsLoading, dataUpdatedAt } = useStats(window);
  const { data: tokens, isLoading: tokensLoading } = useTokenAnalysis(window);
  const { data: agentRoster = [] } = useAgents();
  const { data: dlpAlerts = [] } = useDlpAlerts();
  const { shortLabel: agentShort } = useAgentLabel();
  const [metric, setMetric] = useState<Metric>("tokens");
  // Detail modal state. Clicking a table row opens the modal; the
  // useTopologyAgent hook below fires when detailAgentId is non-null.
  const [detailAgentId, setDetailAgentId] = useState<string | null>(null);

  const isLoading = statsLoading || tokensLoading;

  const activityData = useMemo(() => {
    if (!stats?.activity) return [];
    return Object.entries(stats.activity)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-30)
      .map(([k, v]) => ({ date: k.slice(5), count: v, _full: k }));
  }, [stats]);

  const baseline = useMemo(
    () => computeBaseline(activityData.map((d) => d.count)),
    [activityData],
  );

  // Volume outliers: days whose request count is >2σ from the window
  // mean. This is a capacity/usage signal — useful but unrelated to
  // threats — so it lives as a subtitle on the activity chart rather
  // than a KPI card. The KPI slot below tracks open DLP alerts instead.
  const volumeOutliers = useMemo(() => {
    if (!activityData.length || baseline.stddev === 0) return 0;
    return activityData.filter((d) => Math.abs(d.count - baseline.mean) > 2 * baseline.stddev).length;
  }, [activityData, baseline]);

  // Open DLP alerts in the selected window. /api/dlp-alerts is not
  // window-scoped on the backend, so we filter client-side against
  // created_dt. "Open" matches the threats-alerts page convention:
  // status !== "closed".
  const openAlertsInWindow = useMemo(() => {
    if (!dlpAlerts.length) return 0;
    const cutoff = windowCutoffMs(window);
    return dlpAlerts.filter((a) => {
      if (a.status === "closed") return false;
      const t = new Date(a.created_dt).getTime();
      return Number.isFinite(t) && t >= cutoff;
    }).length;
  }, [dlpAlerts, window]);

  const byAgentData = useMemo(() => {
    // Tokens view pulls from tokens.by_agent (sum of prompt+completion);
    // Calls view pulls from stats.agents (entry counts). Both produce the
    // same shape so the chart binding doesn't change.
    if (metric === "tokens" && tokens?.by_agent) {
      return Object.entries(tokens.by_agent)
        .map(([k, v]) => ({
          agent: agentShort(k),
          count: v.prompt_tokens + v.completion_tokens,
          _id: k,
        }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 8);
    }
    if (!stats?.agents) return [];
    return Object.entries(stats.agents)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([k, v]) => ({ agent: agentShort(k), count: v, _id: k }));
  }, [stats, tokens, agentShort, metric]);

  const byModelData = useMemo(() => {
    if (!tokens?.by_model) return [];
    return Object.entries(tokens.by_model)
      .map(([k, v]) => ({ name: truncate(k, 20), value: v.prompt_tokens + v.completion_tokens }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  }, [tokens]);

  const byProviderData = useMemo(() => {
    if (!tokens?.by_upstream) return [];
    return Object.entries(tokens.by_upstream)
      .map(([k, v]) => ({ name: rewriteProvider(k), value: v.prompt_tokens + v.completion_tokens }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);
  }, [tokens]);

  // Action mix — which entry kinds the gateway routed in this window.
  // `stats.action_types` is a raw `action_type → count` map from the
  // ledger; we relabel known types via ACTION_TYPE_LABEL and let
  // anything unrecognised fall through as its raw key so new action
  // types surface in the chart even before they get a friendly label.
  const byActionData = useMemo(() => {
    if (!stats?.action_types) return [];
    return Object.entries(stats.action_types)
      .map(([k, v]) => ({
        name: ACTION_TYPE_LABEL[k as ActionType] ?? k,
        value: v,
      }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value);
  }, [stats]);

  // Extended table data: merge the token-analysis bucket (tokens / calls per
  // agent) with the agents-roster row (first_seen / last_seen / session_count)
  // by agent_id. Agents that show up in only one source still render, with
  // "—" for the missing columns.
  const extendedAgentRows = useMemo(() => {
    const byId = new Map<string, {
      id: string;
      label: string;
      tokens: number;
      requests: number;
      sessions: number;
      first_seen: string | null;
      last_seen: string | null;
    }>();
    for (const [agentId, bucket] of Object.entries(tokens?.by_agent ?? {})) {
      byId.set(agentId, {
        id: agentId,
        label: agentShort(agentId),
        tokens: bucket.prompt_tokens + bucket.completion_tokens,
        requests: bucket.requests ?? 0,
        sessions: 0,
        first_seen: null,
        last_seen: null,
      });
    }
    for (const a of agentRoster) {
      const existing = byId.get(a.agent_id) ?? {
        id: a.agent_id,
        label: agentShort(a.agent_id),
        tokens: 0,
        requests: 0,
        sessions: 0,
        first_seen: null,
        last_seen: null,
      };
      existing.sessions = a.session_count;
      existing.first_seen = a.first_seen_dt;
      existing.last_seen = a.last_seen_dt;
      byId.set(a.agent_id, existing);
    }
    return Array.from(byId.values()).sort(
      (a, b) => (metric === "tokens" ? b.tokens - a.tokens : b.requests - a.requests),
    );
  }, [tokens, agentRoster, agentShort, metric]);

  // "Active" if last_seen is within the past 24h. Cheap heuristic so the
  // table has a useful Status column without needing a separate endpoint.
  const ACTIVE_WINDOW_MS = 24 * 60 * 60 * 1000;
  const isActive = (lastSeen: string | null) => {
    if (!lastSeen) return false;
    const t = new Date(lastSeen).getTime();
    if (!Number.isFinite(t)) return false;
    return Date.now() - t < ACTIVE_WINDOW_MS;
  };

  if (isLoading) {
    return (
      <>
        <PageHeader title="Agent Activity" description="Forensic view of agent behaviour and usage patterns." />
        <div className="grid grid-cols-4 gap-3 mb-7">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
        <Skeleton className="h-56 rounded-lg mb-7" />
        <div className="grid grid-cols-3 gap-4 mb-7">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-lg" />)}
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Agent Activity"
        description="Forensic view of agent behavior, token usage, and anomaly patterns."
        lastUpdated={dataUpdatedAt}
        actions={
          <div className="flex items-center gap-2">
            <Select value={window} onValueChange={(v) => setWindow(v as StatsWindow)}>
              <SelectTrigger className="h-9 w-36 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATS_WINDOWS.map((w) => (
                  <SelectItem key={w} value={w}>{WINDOW_LABEL[w]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="inline-flex rounded-md border border-border bg-card p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setMetric("tokens")}
                className={cn(
                  "rounded px-3 py-1 font-medium transition-colors",
                  metric === "tokens"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Tokens
              </button>
              <button
                type="button"
                onClick={() => setMetric("calls")}
                className={cn(
                  "rounded px-3 py-1 font-medium transition-colors",
                  metric === "calls"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Calls
              </button>
            </div>
          </div>
        }
      />

      {/* KPI block */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        <MetricCard label="Active Agents" value={stats?.unique_agents ?? 0} to="/agents" />
        <MetricCard label="Total Sessions" value={stats?.unique_sessions ?? 0} to="/sessions" />
        <MetricCard
          label={`Open Alerts (${WINDOW_SHORT[window]})`}
          value={openAlertsInWindow}
          to="/threats-alerts"
        />
        <MetricCard label="Total Tokens" value={fmtTokens(tokens?.total_tokens ?? 0)} />
      </div>

      {/* Activity chart with baseline */}
      <div className="mb-7">
        <ChartCard
          title={`Agent Activity — ${WINDOW_LABEL[window].toLowerCase()}`}
          subtitle={
            volumeOutliers > 0
              ? `${volumeOutliers} volume outlier${volumeOutliers === 1 ? "" : "s"} (>2σ from window mean) — capacity signal, not a threat metric`
              : "No volume outliers in this window"
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={activityData} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" />
              <XAxis dataKey="date" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <ReferenceLine
                y={baseline.mean}
                stroke="hsl(var(--chart-axis))"
                strokeDasharray="4 4"
                label={{ value: "avg", fontSize: 10, fill: "hsl(var(--chart-axis))", fontFamily: "var(--font-mono)" }}
              />
              <ReferenceLine
                y={baseline.mean + 2 * baseline.stddev}
                stroke="hsl(var(--chart-axis))"
                strokeDasharray="2 4"
                strokeOpacity={0.7}
                label={{ value: "+2σ", fontSize: 9, fill: "hsl(var(--chart-axis))", fontFamily: "var(--font-mono)" }}
              />
              <Line type="monotone" dataKey="count" stroke="hsl(var(--chart-line))" strokeWidth={2.5} strokeLinejoin="round" dot={{ r: 2, fill: "hsl(var(--chart-line))" }} name="Entries" />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Breakdown charts — 2x2 on lg, 4-wide on xl. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-4 mb-7">
        <ChartCard title={metric === "tokens" ? "Top Agents by Tokens" : "Top Agents by Calls"} height={240}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={byAgentData} layout="vertical" margin={{ top: 4, right: 12, left: 12, bottom: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" horizontal={false} />
              <XAxis type="number" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis type="category" dataKey="agent" width={100} tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "hsl(var(--chart-track))" }} />
              <Bar dataKey="count" fill="hsl(var(--chart-1))" radius={1} background={{ fill: "hsl(var(--chart-track))" }} name="Requests" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Token Share by Model" height={240}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={byModelData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={70}
                label={({ percent }) => percent > 0.08 ? `${(percent * 100).toFixed(0)}%` : ""}
                labelLine={false}
              >
                {byModelData.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={fmtTokens} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Token Share by AI Provider" height={240}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={byProviderData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={70}
                label={({ percent }) => percent > 0.08 ? `${(percent * 100).toFixed(0)}%` : ""}
                labelLine={false}
              >
                {byProviderData.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={fmtTokens} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Action Mix" height={240}>
          {byActionData.length === 0 ? (
            <p className="text-xs text-muted-foreground py-12 text-center">
              No actions in this window.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={byActionData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={70}
                  label={({ percent }) => percent > 0.08 ? `${(percent * 100).toFixed(0)}%` : ""}
                  labelLine={false}
                >
                  {byActionData.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v: number) => v.toLocaleString()}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
      </div>

      {/* Agent detail table — extended with first/last seen + sessions.
          "Models Used" is intentionally absent until the backend exposes a
          per-agent model breakdown (see "Agent Detail View" in the
          deferred polish list). */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">
            Agent Detail {metric === "tokens" ? "(by Tokens)" : "(by Calls)"}
          </h2>
        </div>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="text-right">Sessions</TableHead>
                <TableHead className="text-right">
                  {metric === "tokens" ? "Tokens" : "Calls"}
                </TableHead>
                <TableHead>First Seen</TableHead>
                <TableHead>Last Active</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {extendedAgentRows.slice(0, 25).map((r) => {
                const value = metric === "tokens" ? r.tokens : r.requests;
                const active = isActive(r.last_seen);
                return (
                  <TableRow
                    key={r.id}
                    className="cursor-pointer hover:bg-accent/40"
                    onClick={() => setDetailAgentId(r.id)}
                  >
                    <TableCell className="font-mono text-xs max-w-[200px] truncate" title={r.id}>
                      {r.label}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {r.sessions || "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs font-semibold">
                      {metric === "tokens" ? fmtTokens(value) : value.toLocaleString()}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                      {r.first_seen ? r.first_seen.slice(0, 10) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                      {r.last_seen ? r.last_seen.slice(0, 16).replace("T", " ") : "—"}
                    </TableCell>
                    <TableCell>
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-medium",
                          active
                            ? "bg-primary/15 text-primary"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        <span
                          className={cn(
                            "h-1.5 w-1.5 rounded-full",
                            active ? "bg-primary" : "bg-muted-foreground",
                          )}
                        />
                        {active ? "active" : "idle"}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>

      <AgentDetailDialog
        agentId={detailAgentId}
        onClose={() => setDetailAgentId(null)}
      />
    </>
  );
}

// Modal-backed agent detail view. Reuses /api/topology/agent/{id} for
// the per-agent breakdown — that endpoint already returns the per-tool /
// upstream / model counts ("Models Used") plus recent sessions, which is
// the bulk of what the Agent Detail View was scoped to.
function AgentDetailDialog({
  agentId,
  onClose,
}: {
  agentId: string | null;
  onClose: () => void;
}) {
  // The TopologyAgent endpoint accepts only the strict topology windows;
  // use 30d as a forensic-friendly default.
  const { data, isLoading } = useTopologyAgent(agentId, "30d");
  const { isAdmin, me } = useMe();
  const { enforcementEnabled } = useFeatures();
  const blockAgent = useBlockAgent();

  const onBlock = async () => {
    if (!agentId) return;
    if (!window.confirm(
      `Block ${agentId}? All future proxy requests from this agent will be rejected.`,
    )) return;
    try {
      await blockAgent.mutateAsync({
        agent_id: agentId,
        reason: `Blocked from Agent Detail by ${me?.username ?? "admin"}`,
      });
      toast.success(`Agent ${agentId} blocked`);
      onClose();
    } catch (err) {
      toast.error((err as Error).message || "Block failed");
    }
  };

  return (
    <Dialog open={agentId !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        {agentId && (
          <>
            <DialogHeader>
              <DialogTitle className="font-mono text-sm">
                {data?.agent_id ?? agentId}
              </DialogTitle>
              <DialogDescription>
                Last 30 days · {data?.request_count?.toLocaleString() ?? "—"} requests
                {data?.last_seen_iso ? ` · last seen ${data.last_seen_iso.slice(0, 19).replace("T", " ")}` : ""}
              </DialogDescription>
              <Link
                to={`/agents/${encodeURIComponent(agentId)}`}
                onClick={onClose}
                className="text-xs text-primary hover:underline mt-1 self-start"
              >
                Open full view ↗
              </Link>
            </DialogHeader>

            {isLoading || !data ? (
              <p className="text-sm text-muted-foreground py-6 text-center">Loading…</p>
            ) : (
              <div className="space-y-6 text-sm">
                <div className="grid grid-cols-2 gap-4">
                  <Section title="Models used">
                    {data.models.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No model usage observed.</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.models.slice(0, 8).map((m) => (
                          <li key={m.name} className="flex justify-between gap-2">
                            <span className="font-mono text-xs truncate">{m.name}</span>
                            <span className="font-mono text-xs text-muted-foreground">{m.count}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Section>
                  <Section title="Tools">
                    {data.tools.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No tools observed.</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.tools.slice(0, 8).map((t) => (
                          <li key={t.name} className="flex justify-between gap-2">
                            <span className="font-mono text-xs truncate">{t.name}</span>
                            <span className="font-mono text-xs text-muted-foreground">{t.count}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Section>
                </div>

                <Section title="AI providers">
                  {data.upstreams.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No upstream data.</p>
                  ) : (
                    <ul className="space-y-1">
                      {data.upstreams.map((u) => (
                        <li key={u.name} className="flex justify-between gap-2">
                          <span className="font-mono text-xs">{u.name}</span>
                          <span className="font-mono text-xs text-muted-foreground">{u.count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>

                <Section title="Recent sessions">
                  {data.sessions.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No recent sessions.</p>
                  ) : (
                    <ul className="space-y-1">
                      {data.sessions.slice(0, 10).map((s) => (
                        <li key={s.session_id} className="flex justify-between gap-2">
                          <a
                            href={`/sessions/${s.session_id}`}
                            className="font-mono text-xs text-primary hover:underline truncate"
                          >
                            {s.session_id}
                          </a>
                          <span className="font-mono text-xs text-muted-foreground">
                            {s.request_count}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>

                <div className="flex justify-end gap-2 pt-2 border-t">
                  {isAdmin && (
                    <PaidLock
                      locked={!enforcementEnabled}
                      hint="Agent blocking is part of enforcement — available in the KYDE Enterprise edition. The sandbox edition is observe-only."
                    >
                      <Button
                        variant="outline"
                        className="border-destructive text-destructive hover:bg-destructive/10"
                        onClick={onBlock}
                        disabled={blockAgent.isPending}
                      >
                        Block agent
                      </Button>
                    </PaidLock>
                  )}
                  <Button variant="outline" onClick={onClose}>Close</Button>
                </div>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}
