import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  BarChart,
  Bar,
} from "recharts";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ChartCard } from "@/components/shared/chart-card";
import { RelativeTime } from "@/components/shared/relative-time";
import { Skeleton } from "@/components/ui/skeleton";
import { useDlpAlerts, useFleetTrust, useStats, useVerify } from "@/api/queries";
import { TrustScoreHero } from "@/components/shared/trust-score";
import { formatAlertId } from "@/lib/serial-ids";
import { useAgentLabel } from "@/hooks/use-agent-label";
import { useFeatures } from "@/hooks/use-features";
import { LockedMetric } from "@/components/shared/upgrade-lock";
import { cn } from "@/lib/utils";
import { getSeverity } from "@/components/shared/dlp-alert-detail";

const GRID_STROKE = "hsl(var(--chart-grid))";
const TICK_STYLE = { fill: "hsl(var(--chart-axis))", fontSize: 11, fontFamily: "var(--font-mono)" };
// Monochrome blue ramp — saturated (rank 1) → light (rank 5). No severity color.
const RAMP = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];

function computeBaseline(values: number[]) {
  if (!values.length) return { mean: 0, stddev: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / values.length;
  return { mean, stddev: Math.sqrt(variance) };
}

// Severity here drives alert-context text (filter chips, recent-activity rows),
// so it keeps color — but on the Editorial Mono severity palette, not raw red/amber.
const SEV_COLOR: Record<string, string> = {
  CRITICAL: "text-sev-critical",
  HIGH:     "text-sev-high",
  MEDIUM:   "text-sev-medium",
  LOW:      "text-sev-low",
};

export default function FleetStatusPage() {
  // Fleet Status's "last 14 days" activity chart needs a window wide enough
  // to cover that slice — 30d gives headroom while keeping queries scoped.
  const { data: stats, isLoading: statsLoading } = useStats("30d");
  const { data: verify, isLoading: verifyLoading } = useVerify();
  const { data: trust, isLoading: trustLoading } = useFleetTrust("30d");
  const { signingEnabled } = useFeatures();
  const { data: alerts = [], isLoading: alertsLoading } = useDlpAlerts();
  const { shortLabel: agentShort } = useAgentLabel();
  const navigate = useNavigate();

  const isLoading = statsLoading || verifyLoading || alertsLoading || trustLoading;

  const openAlerts = alerts.filter((a) => a.status !== "closed");
  const criticalAlerts = openAlerts.filter((a) => getSeverity(a) === "CRITICAL");
  const mediumAlerts = openAlerts.filter((a) => {
    const s = getSeverity(a);
    return s === "HIGH" || s === "MEDIUM";
  });

  const operationalStatus: "BREACH" | "WARNING" | "OPERATIONAL" = useMemo(() => {
    // Integrity verification is an Enterprise feature — only let it drive the
    // breach state when signing is enabled. In the sandbox edition, status is driven by
    // detection alerts alone.
    if ((signingEnabled && verify?.valid === false) || criticalAlerts.length > 0)
      return "BREACH";
    if (mediumAlerts.length > 0) return "WARNING";
    return "OPERATIONAL";
  }, [signingEnabled, verify, criticalAlerts.length, mediumAlerts.length]);

  // Minimal banner (spec §6.4): a status dot + eyebrow + line, no pastel fill.
  // Color is carried only by the dot + eyebrow (alert context), on-palette.
  const statusTone = {
    BREACH:      "text-sev-critical",
    WARNING:     "text-sev-medium",
    OPERATIONAL: "text-sev-low",
  }[operationalStatus];
  const statusDot = {
    BREACH:      "bg-sev-critical",
    WARNING:     "bg-sev-medium",
    OPERATIONAL: "bg-sev-low",
  }[operationalStatus];

  const contextLine = {
    BREACH:      `${criticalAlerts.length} critical incident(s) in progress · Immediate action required`,
    WARNING:     `${mediumAlerts.length} alert(s) require attention · Review recommended`,
    OPERATIONAL: `All systems normal · ${stats?.unique_agents ?? 0} active agents · Last scan 2 minutes ago`,
  }[operationalStatus];

  const activityData = useMemo(() => {
    if (!stats?.activity) return [];
    return Object.entries(stats.activity)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-14)
      .map(([k, v]) => ({ date: k.slice(5), count: v }));
  }, [stats]);

  const baseline = useMemo(
    () => computeBaseline(activityData.map((d) => d.count)),
    [activityData],
  );

  // Top-5 agents by activity. Bars use the monochrome blue ramp by rank
  // (most active = most saturated) — not severity color (spec §5).
  const agentData = useMemo(() => {
    if (!stats?.agents) return [];
    return Object.entries(stats.agents)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5)
      .map(([k, v]) => ({ agent: agentShort(k), count: v }));
  }, [stats, agentShort]);

  // Data points that fall outside the baseline corridor (±2σ) are flagged
  // and rendered as larger red dots. Empty stddev (1-row data) skips the
  // anomaly logic so we don't paint everything red on day-one deploys.
  const activityWithAnomaly = useMemo(() => {
    if (!activityData.length) return [];
    if (baseline.stddev === 0) return activityData.map((d) => ({ ...d, anomaly: false }));
    return activityData.map((d) => ({
      ...d,
      anomaly: Math.abs(d.count - baseline.mean) > 2 * baseline.stddev,
    }));
  }, [activityData, baseline]);
  const corridor = useMemo(
    () => ({
      low: Math.max(0, baseline.mean - 2 * baseline.stddev),
      high: baseline.mean + 2 * baseline.stddev,
    }),
    [baseline],
  );

  // Severity filter chips above the activity feed. "all" means show
  // everything; clicking an active chip resets to "all".
  const [sevFilter, setSevFilter] = useState<"all" | "CRITICAL" | "HIGH" | "MEDIUM" | "LOW">("all");
  // Chips + recent-activity reflect current posture, not historical pile —
  // closed alerts are handled, not "active threats". Use the Threats &
  // Alerts page's Closed tab to audit past dispositions.
  const sevCounts = useMemo(() => {
    const c: Record<string, number> = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
    for (const a of openAlerts) c[getSeverity(a)]++;
    return c;
  }, [openAlerts]);
  const recentAlerts = useMemo(() => {
    const sorted = [...openAlerts].sort(
      (a, b) => new Date(b.created_dt).getTime() - new Date(a.created_dt).getTime(),
    );
    const matched = sevFilter === "all"
      ? sorted
      : sorted.filter((a) => getSeverity(a) === sevFilter);
    return matched.slice(0, 8);
  }, [openAlerts, sevFilter]);

  if (isLoading) {
    return (
      <>
        <PageHeader title="Workforce Status" description="System trust and active threat overview." />
        <Skeleton className="h-56 w-full rounded-lg mb-7" />
        <Skeleton className="h-28 w-full mb-7" />
        <div className="grid grid-cols-4 gap-3 mb-7">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-64 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Workforce Status" description="System trust and active threat overview." />

      {/* Fleet Trust Score — composite (5-dimension formula) with the
          labeled gauge and per-dimension scales that feed it. */}
      {trust ? (
        <div className="mb-7">
          <TrustScoreHero
            score={trust.trust_score}
            tierKey={trust.tier_key}
            tier={trust.tier}
            caption={`across ${trust.active_agents} active agent${trust.active_agents === 1 ? "" : "s"}`}
            dimensions={trust.dimensions}
          />
        </div>
      ) : null}

      {/* Operational status — minimal banner (spec §6.4) */}
      <div className="mb-7 flex items-center gap-3 border-b border-border pb-4">
        <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", statusDot)} />
        <span className={cn("eyebrow", statusTone)}>{operationalStatus}</span>
        <span className="text-[15px] font-semibold text-foreground">{contextLine}</span>
      </div>

      {/* KPI block */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        <MetricCard label="Active Agents" value={stats?.unique_agents ?? 0} />
        <button className="text-left" onClick={() => navigate("/threats-alerts")}>
          <MetricCard label="Open Alerts" value={openAlerts.length} />
        </button>
        <button className="text-left" onClick={() => navigate("/agent-chains")}>
          <MetricCard label="Blocked Chains (24h)" value={criticalAlerts.length} />
        </button>
        {signingEnabled ? (
          <MetricCard
            label="Data Integrity"
            value={verify?.valid ? "VERIFIED" : "BROKEN"}
            tone={verify?.valid ? "success" : "destructive"}
          />
        ) : (
          <LockedMetric label="Data Integrity" />
        )}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 mb-7">
        <ChartCard title="Agent Activity (last 14 days)">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={activityWithAnomaly} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <defs>
                <linearGradient id="activityFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="hsl(var(--chart-line))" stopOpacity={0.14} />
                  <stop offset="100%" stopColor="hsl(var(--chart-line))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" />
              <XAxis dataKey="date" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip
                contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12, fontFamily: "var(--font-mono)" }}
              />
              {/* Grey baseline corridor: mean ±2σ. Days that fall outside
                  this band get the anomaly dot below. */}
              {baseline.stddev > 0 ? (
                <ReferenceArea
                  y1={corridor.low}
                  y2={corridor.high}
                  fill="hsl(var(--chart-axis))"
                  fillOpacity={0.08}
                  stroke="none"
                />
              ) : null}
              <ReferenceLine y={baseline.mean} stroke="hsl(var(--chart-axis))" strokeDasharray="4 4" label={{ value: "avg", fontSize: 10, fill: "hsl(var(--chart-axis))", fontFamily: "var(--font-mono)" }} />
              <Area
                type="monotone"
                dataKey="count"
                stroke="hsl(var(--chart-line))"
                strokeWidth={2.5}
                strokeLinejoin="round"
                fill="url(#activityFill)"
                dot={(props: { cx?: number; cy?: number; payload?: { anomaly?: boolean } }) => {
                  const { cx, cy, payload } = props;
                  if (cx === undefined || cy === undefined) return <g />;
                  if (payload?.anomaly) {
                    // The single allowed non-blue accent in a chart: the peak marker.
                    return (
                      <circle
                        cx={cx}
                        cy={cy}
                        r={4.5}
                        fill="hsl(var(--chart-marker))"
                        stroke="hsl(var(--background))"
                        strokeWidth={1.5}
                      />
                    );
                  }
                  return <circle cx={cx} cy={cy} r={2} fill="hsl(var(--chart-line))" />;
                }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Top 5 Active Agents">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={agentData} layout="vertical" margin={{ top: 8, right: 12, left: 12, bottom: 0 }} barCategoryGap="38%">
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" horizontal={false} />
              <XAxis type="number" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis type="category" dataKey="agent" width={130} tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12, fontFamily: "var(--font-mono)" }} cursor={{ fill: "hsl(var(--chart-track))" }} />
              {/* Monochrome blue ramp by rank — most active = most saturated. */}
              <Bar dataKey="count" radius={1} background={{ fill: "hsl(var(--chart-track))" }}>
                {agentData.map((_, i) => (
                  <Cell key={i} fill={RAMP[Math.min(i, RAMP.length - 1)]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Recent activity feed */}
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold">Recent Activity</h2>
          {/* Severity chips — single-select with implicit All. Clicking the
              active chip resets back to "all" so the row behaves like a
              radio without an explicit deselect button. */}
          <div className="flex flex-wrap items-center gap-1">
            {(["all", "CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((sev) => {
              const active = sevFilter === sev;
              const count = sev === "all" ? alerts.length : sevCounts[sev];
              const baseClasses =
                "rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors";
              const colorClass = sev !== "all" ? SEV_COLOR[sev] : "";
              return (
                <button
                  key={sev}
                  type="button"
                  onClick={() => setSevFilter(active && sev !== "all" ? "all" : sev)}
                  className={cn(
                    baseClasses,
                    active
                      ? "border-foreground bg-foreground/10"
                      : "border-border bg-card hover:border-foreground/40",
                    colorClass,
                  )}
                  aria-pressed={active}
                >
                  {sev === "all" ? "All" : sev}
                  <span className="ml-1.5 text-muted-foreground">{count}</span>
                </button>
              );
            })}
          </div>
        </div>
        {recentAlerts.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            {sevFilter === "all"
              ? "No recent activity."
              : `No ${sevFilter} alerts in the feed.`}
          </p>
        ) : (
          <div className="rounded-md border divide-y">
            {recentAlerts.map((alert) => {
              const sev = getSeverity(alert);
              return (
                <div
                  key={String(alert.id)}
                  onClick={() => navigate("/threats-alerts")}
                  className="flex items-center justify-between px-4 py-3 text-sm cursor-pointer hover:bg-accent/40 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-muted-foreground text-xs shrink-0">
                      <RelativeTime value={alert.created_dt} />
                    </span>
                    <span className={cn("font-semibold text-xs shrink-0", SEV_COLOR[sev])}>{sev}</span>
                    <span className="text-muted-foreground shrink-0">·</span>
                    <span className="truncate text-muted-foreground">
                      {agentShort(alert.entry_id ?? alert.session_id ?? "unknown")}
                    </span>
                  </div>
                  <span className="font-mono text-xs text-muted-foreground shrink-0 ml-4">
                    {formatAlertId(alert.serial_id ?? alert.id)} →
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
