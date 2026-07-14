import { useState } from "react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ChartCard } from "@/components/shared/chart-card";
import {
  DateRangePicker,
  filterByRange,
  type DateRange,
} from "@/components/shared/date-range-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { useStats, useVerify } from "@/api/queries";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { truncate } from "@/lib/format";

const CHART_COLORS = ["hsl(var(--chart-1))", "hsl(var(--chart-2))", "hsl(var(--chart-3))", "hsl(var(--chart-4))", "hsl(var(--chart-5))"];
const GRID_STROKE = "hsl(var(--border))";
const TICK_STYLE = { fill: "hsl(var(--muted-foreground))", fontSize: 11 };

export default function OverviewPage() {
  // Overview is a high-level snapshot — last 7 days strikes a balance
  // between "right now" and "what's been happening lately."
  const { data: stats, isLoading, dataUpdatedAt } = useStats("7d");
  const { data: verify } = useVerify();
  const [range, setRange] = useState<DateRange | undefined>();

  if (isLoading || !stats) {
    return (
      <>
        <PageHeader title="Overview" description="Ledger summary and behavioral analytics" />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6 mb-7">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-72 rounded-lg" />)}
        </div>
      </>
    );
  }

  const chainIntact = verify?.valid ?? false;

  const filteredActivity = filterByRange(stats.activity, range, 10);
  const activityData = Object.keys(filteredActivity)
    .sort()
    .map((k) => ({ date: k.slice(5), count: filteredActivity[k] }));

  const agentData = Object.entries(stats.agents)
    .map(([k, v]) => ({ agent: truncate(k, 16), count: v }))
    .sort((a, b) => b.count - a.count);

  const actionData = Object.entries(stats.action_types).map(([k, v]) => ({ name: k, value: v }));
  const upstreamData = Object.entries(stats.upstreams).map(([k, v]) => ({ name: k, value: v }));

  return (
    <>
      <PageHeader
        title="Overview"
        description="Ledger summary and behavioral analytics"
        lastUpdated={dataUpdatedAt}
        actions={<DateRangePicker value={range} onChange={setRange} />}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6 mb-7">
        <MetricCard label="Total Entries"    value={stats.total} />
        <MetricCard label="First Entry"      small value={stats.first_entry ?? "-"} />
        <MetricCard label="Last Entry"       small value={stats.last_entry ?? "-"} />
        <MetricCard label="Unique Agents"    value={stats.unique_agents} />
        <MetricCard label="Unique Sessions"  value={stats.unique_sessions} />
        <MetricCard
          label="Data Integrity"
          value={chainIntact ? "VERIFIED" : "BROKEN"}
          tone={chainIntact ? "success" : "destructive"}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Activity Over Time">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={activityData} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip
                contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }}
                labelStyle={{ color: "hsl(var(--foreground))" }}
              />
              <Line type="monotone" dataKey="count" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={{ r: 2 }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Entries Per Agent">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={agentData} layout="vertical" margin={{ top: 8, right: 12, left: 12, bottom: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <YAxis type="category" dataKey="agent" width={100} tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
              <Tooltip contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
              <Bar dataKey="count" fill="hsl(var(--chart-4))" radius={[0, 2, 2, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Action Types">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={actionData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={2}>
                {actionData.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Legend verticalAlign="middle" align="right" layout="vertical" iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              <Tooltip contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Upstream Providers">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={upstreamData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={2}>
                {upstreamData.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Legend verticalAlign="middle" align="right" layout="vertical" iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              <Tooltip contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </>
  );
}
