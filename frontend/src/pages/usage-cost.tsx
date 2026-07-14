import { useMemo, useState } from "react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ChartCard } from "@/components/shared/chart-card";
import {
  DateRangePicker,
  filterByRange,
  type DateRange,
} from "@/components/shared/date-range-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTokenAnalysis } from "@/api/queries";
import { fmtTokens, truncate } from "@/lib/format";
import { useAgentLabel } from "@/hooks/use-agent-label";
import type { TokenBucket } from "@/api/types";

const GRID_STROKE = "hsl(var(--chart-grid))";
const TICK_STYLE = { fill: "hsl(var(--chart-axis))", fontSize: 11, fontFamily: "var(--font-mono)" };
const TOOLTIP_STYLE = {
  background: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 6,
  fontSize: 12,
  fontFamily: "var(--font-mono)",
};
const LEGEND_STYLE = { fontSize: 12, fontFamily: "var(--font-mono)" };
// Two blue tones (spec §5): Prompt = light, Completion = saturated.
const PROMPT_FILL = "hsl(var(--chart-4))";
const COMPLETION_FILL = "hsl(var(--chart-1))";

function rewriteProvider(raw: string): string {
  if (!raw || raw === "(none)") return "Direct (no provider)";
  if (raw.includes("anthropic")) return "Anthropic";
  if (raw.includes("openai")) return "OpenAI";
  if (raw.includes("google")) return "Google";
  if (raw.includes("azure")) return "Azure OpenAI";
  if (raw.includes("bedrock")) return "AWS Bedrock";
  return raw;
}

function computeBaseline(values: number[]) {
  if (!values.length) return { mean: 0, stddev: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / values.length;
  return { mean, stddev: Math.sqrt(variance) };
}

function bucketRows(obj: Record<string, TokenBucket>, labelTransform?: (k: string) => string) {
  return Object.entries(obj)
    .map(([k, v]) => ({
      name: labelTransform ? labelTransform(k) : k,
      prompt: v.prompt_tokens,
      completion: v.completion_tokens,
      total: v.prompt_tokens + v.completion_tokens,
    }))
    .sort((a, b) => b.total - a.total);
}

function HorizontalStackedBar({
  data,
  nameWidth = 130,
}: {
  data: Array<{ name: string; prompt: number; completion: number }>;
  nameWidth?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={data.map((d) => ({ ...d, name: truncate(d.name, 22) }))}
        layout="vertical"
        margin={{ top: 8, right: 12, left: 12, bottom: 0 }}
      >
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" horizontal={false} />
        <XAxis type="number" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} tickFormatter={fmtTokens} />
        <YAxis type="category" dataKey="name" width={nameWidth} tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => fmtTokens(v)} />
        <Legend wrapperStyle={LEGEND_STYLE} />
        <Bar dataKey="prompt" stackId="t" fill={PROMPT_FILL} name="Prompt" radius={[0, 1, 1, 0]} />
        <Bar dataKey="completion" stackId="t" fill={COMPLETION_FILL} name="Completion" radius={[0, 1, 1, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function DetailTable({ rows }: { rows: Array<{ name: string; prompt: number; completion: number; total: number }> }) {
  const [showAll, setShowAll] = useState(false);
  const LIMIT = 10;
  const visible = showAll ? rows : rows.slice(0, LIMIT);
  const hidden = rows.length - visible.length;

  return (
    <div className="rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Agent</TableHead>
            <TableHead className="text-right">Prompt</TableHead>
            <TableHead className="text-right">Completion</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((r) => (
            <TableRow key={r.name}>
              <TableCell className="font-mono text-xs max-w-[200px] truncate">{r.name}</TableCell>
              <TableCell className="text-right font-mono text-xs">{fmtTokens(r.prompt)}</TableCell>
              <TableCell className="text-right font-mono text-xs">{fmtTokens(r.completion)}</TableCell>
              <TableCell className="text-right font-mono text-xs">{fmtTokens(r.total)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hidden > 0 && (
        <div className="border-t border-border px-4 py-2 text-center">
          <button
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowAll(true)}
          >
            Show {hidden} more agents →
          </button>
        </div>
      )}
    </div>
  );
}

export default function UsageCostPage() {
  // 7d gives enough hourly buckets for the 48-bar chart and matches the
  // typical "last week's cost" mental model. The DateRangePicker further
  // narrows client-side via filterByRange.
  const { data, isLoading, dataUpdatedAt } = useTokenAnalysis("7d");
  const { shortLabel: agentShort } = useAgentLabel();
  const [range, setRange] = useState<DateRange | undefined>();

  const hourlyData = useMemo(() => {
    if (!data?.by_hour) return [];
    const source = range ? filterByRange(data.by_hour, range, 13) : data.by_hour;
    return Object.entries(source)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-48)
      .map(([k, v]) => ({
        hour: k.slice(11, 16),
        prompt: v.prompt_tokens,
        completion: v.completion_tokens,
        total: v.prompt_tokens + v.completion_tokens,
      }));
  }, [data, range]);

  const baseline = useMemo(
    () => computeBaseline(hourlyData.map((d) => d.total)),
    [hourlyData],
  );

  const byAgentRows = useMemo(
    () => (data?.by_agent ? bucketRows(data.by_agent, agentShort) : []),
    [data, agentShort],
  );

  const byModelRows = useMemo(
    () => (data?.by_model ? bucketRows(data.by_model) : []),
    [data],
  );

  const byProviderRows = useMemo(
    () => (data?.by_upstream ? bucketRows(data.by_upstream, rewriteProvider) : []),
    [data],
  );

  if (isLoading) {
    return (
      <>
        <PageHeader title="Token Usage" description="Token consumption by agent, model, and provider." />
        <div className="grid grid-cols-3 gap-3 mb-7">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
        <Skeleton className="h-64 rounded-lg mb-7" />
        <div className="grid grid-cols-3 gap-4 mb-7">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-lg" />)}
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Token Usage"
        description="Token consumption by agent, model, and AI provider."
        lastUpdated={dataUpdatedAt}
        actions={<DateRangePicker value={range} onChange={setRange} />}
      />

      {/* KPI block — token counts only. */}
      <div className="grid grid-cols-3 gap-3 mb-7">
        <MetricCard label="Total Tokens" value={fmtTokens(data?.total_tokens ?? 0)} />
        <MetricCard
          label="Prompt / Completion"
          value={
            fmtTokens(data?.total_prompt_tokens ?? 0) +
            " / " +
            fmtTokens(data?.total_completion_tokens ?? 0)
          }
        />
        <MetricCard label="Active Agents" value={Object.keys(data?.by_agent ?? {}).length} />
      </div>

      {/* Hourly usage with baseline */}
      <div className="mb-7">
        <ChartCard title="Token Usage Over Time">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={hourlyData} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" />
              <XAxis dataKey="hour" tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={TICK_STYLE} axisLine={{ stroke: GRID_STROKE }} tickLine={false} tickFormatter={fmtTokens} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => fmtTokens(v)} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <ReferenceLine
                y={baseline.mean}
                stroke="hsl(var(--chart-axis))"
                strokeDasharray="4 4"
                label={{ value: "avg", fontSize: 10, fill: "hsl(var(--chart-axis))", fontFamily: "var(--font-mono)" }}
              />
              <Bar dataKey="prompt" stackId="t" fill={PROMPT_FILL} name="Prompt" />
              <Bar dataKey="completion" stackId="t" fill={COMPLETION_FILL} name="Completion" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Three breakdown charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 mb-7">
        <ChartCard title="By Agent" height={200}>
          <HorizontalStackedBar data={byAgentRows.slice(0, 6)} nameWidth={110} />
        </ChartCard>
        <ChartCard title="By Model" height={200}>
          <HorizontalStackedBar data={byModelRows.slice(0, 6)} nameWidth={110} />
        </ChartCard>
        <ChartCard title="By AI Provider" height={200}>
          <HorizontalStackedBar data={byProviderRows.slice(0, 6)} nameWidth={110} />
        </ChartCard>
      </div>

      {/* Agent detail table */}
      <div>
        <h2 className="text-sm font-semibold mb-3">Agent Breakdown</h2>
        <DetailTable rows={byAgentRows} />
      </div>
    </>
  );
}
