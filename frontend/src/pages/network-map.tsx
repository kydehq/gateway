import { useMemo, useState } from "react";
import { ResponsiveContainer, Sankey, Tooltip, PieChart, Pie, Cell } from "recharts";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ChartCard } from "@/components/shared/chart-card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTopology, useTopologyFlow, useStats } from "@/api/queries";
import type { OriginClass, TopologyLayer, TopologyResponse, TopologyWindow } from "@/api/types";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { NavLink } from "react-router-dom";
import { formatSessionId } from "@/lib/serial-ids";


// Network Map — cool, blue-anchored CATEGORICAL palette (ref
// ~/Pictures/kyde/network-flow.png). A deliberate, scoped exception to the
// monochrome rule: only cool hues, so the flow reads as categorical (by model)
// yet never collides with the warm severity scale — red/amber stay reserved
// for alerts.
const COOL = {
  blue:   "#2563eb",
  indigo: "#4f46e5",
  sky:    "#0ea5e9",
  teal:   "#14b8a6",
  violet: "#7c3aed",
  slate:  "#64748b",
} as const;

// Distinct cool colors, cycled within each column so adjacent streams differ
// (brand-blue anchor first). Nodes themselves render black; this palette colors
// the streams.
const STREAM_PALETTE = [COOL.blue, COOL.sky, COOL.teal, COOL.violet, COOL.indigo, COOL.slate];

const CLASS_LABEL: Record<OriginClass, string> = {
  public: "Public",
  rfc1918: "RFC1918",
  cgnat: "CGNAT",
  loopback: "Loopback",
  link_local: "Link-local",
  unique_local_v6: "IPv6 ULA",
  unknown: "Unknown",
};

function rewriteLabel(raw: string, layer: TopologyLayer): string {
  if (layer === "upstream") {
    if (!raw || raw === "(none)") return "Direct (no provider)";
    if (raw.includes("anthropic")) return "Anthropic";
    if (raw.includes("openai")) return "OpenAI";
    if (raw.includes("google")) return "Google";
    if (raw.includes("azure")) return "Azure OpenAI";
    if (raw.includes("bedrock")) return "AWS Bedrock";
    return raw;
  }
  return raw;
}

type SankeyInput = {
  nodes: Array<{ name: string; id: string; layer: TopologyLayer; klass?: OriginClass }>;
  links: Array<{ source: number; target: number; value: number }>;
};

function toSankeyData(api: TopologyResponse): SankeyInput {
  const nodes = api.nodes.map((n) => ({
    id: n.id,
    name: rewriteLabel(n.label, n.layer),
    layer: n.layer,
    klass: n.meta?.class,
  }));
  const index = new Map(nodes.map((n, i) => [n.id, i] as const));
  const links: SankeyInput["links"] = [];
  for (const link of api.links) {
    const s = index.get(link.source);
    const t = index.get(link.target);
    if (s === undefined || t === undefined) continue;
    if (s === t) continue;
    links.push({ source: s, target: t, value: link.value });
  }
  return { nodes, links };
}

function NetworkNode({
  x, y, width, height, payload,
}: {
  x: number; y: number; width: number; height: number;
  index: number;
  payload: { id: string; name: string; layer: TopologyLayer; klass?: OriginClass; value: number };
}) {
  // Boxed mono label (ref network-flow.png): a light chip with a hairline
  // border, sat just past the black node bar. Width approximated from the
  // label length since SVG has no layout pass.
  const label = payload.name;
  const charW = 6.4;
  const padX = 7;
  const boxW = Math.max(26, label.length * charW + padX * 2);
  const boxH = 18;
  const labelX = x + width + 6;
  const cy = y + height / 2;
  return (
    <g>
      {/* Node "corner" — black bar (ref). */}
      <rect x={x} y={y} width={width} height={height} rx={1} fill="hsl(var(--foreground))" fillOpacity={0.9}>
        <title>
          {payload.name}
          {payload.layer === "segment" && payload.klass ? ` (${CLASS_LABEL[payload.klass]})` : ""}
          {` — ${payload.value.toLocaleString()} requests`}
        </title>
      </rect>
      <rect
        x={labelX}
        y={cy - boxH / 2}
        width={boxW}
        height={boxH}
        rx={4}
        fill="hsl(var(--card))"
        stroke="hsl(var(--border))"
      />
      <text
        x={labelX + padX}
        y={cy}
        dy="0.32em"
        textAnchor="start"
        fill="hsl(var(--foreground))"
        fontSize={11}
        fontFamily="var(--font-mono, ui-monospace)"
      >
        {label}
      </text>
    </g>
  );
}

const TOOLTIP_STYLE = {
  background: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 6,
  fontSize: 12,
  fontFamily: "var(--font-mono)",
};

export default function NetworkMapPage() {
  const [window, setWindow] = useState<TopologyWindow>("24h");
  const { data, isLoading, dataUpdatedAt } = useTopology(window);
  // useStats accepts a superset of topology's windows — passing the page's
  // selected topology window keeps the side pie chart aligned with the
  // Sankey time scope.
  const { data: stats } = useStats(window);

  // Selected Sankey link → drives the side panel. Cleared when the user
  // closes the sheet or the underlying topology data reloads.
  const [selectedFlow, setSelectedFlow] = useState<
    { source: { layer: TopologyLayer; label: string }; target: { layer: TopologyLayer; label: string } } | null
  >(null);
  const { data: flowData, isLoading: flowLoading } = useTopologyFlow(
    selectedFlow?.source ?? null,
    selectedFlow?.target ?? null,
    window,
  );

  const sankey = useMemo(
    () => (data ? toSankeyData(data) : null),
    [data],
  );

  // Every node gets a distinct cool color, cycled within its column (so the
  // model column keeps legend order). Each link is colored by its NON-gateway
  // endpoint — because all flows merge through the single gateway node, this is
  // what keeps separate streams visually distinct end to end.
  const nodeColorById = useMemo(() => {
    const m: Record<string, string> = {};
    if (!sankey) return m;
    const perLayer: Record<string, number> = {};
    for (const n of sankey.nodes) {
      const idx = perLayer[n.layer] ?? 0;
      perLayer[n.layer] = idx + 1;
      m[n.id] = STREAM_PALETTE[idx % STREAM_PALETTE.length];
    }
    return m;
  }, [sankey]);

  const colorForNode = (node: { id: string }) => nodeColorById[node.id] ?? COOL.blue;

  const modelLegend = useMemo(
    () =>
      sankey
        ? sankey.nodes
            .filter((n) => n.layer === "model")
            .map((n) => ({ name: n.name, color: nodeColorById[n.id] ?? COOL.blue }))
        : [],
    [sankey, nodeColorById],
  );

  const kpiNodes = data?.nodes.length ?? 0;
  const kpiAgents = useMemo(
    () => data?.nodes.filter((n) => n.layer === "segment").length ?? 0,
    [data],
  );
  const kpiProviders = useMemo(
    () => data?.nodes.filter((n) => n.layer === "upstream").length ?? 0,
    [data],
  );
  const kpiModels = useMemo(
    () => data?.nodes.filter((n) => n.layer === "model").length ?? 0,
    [data],
  );
  // Nodes labeled "unknown" across any layer — the backend uses this label
  // when the relevant column is empty (no upstream host, no UA tool, etc.).
  // Surfacing the count lets operators see how much of the traffic is
  // un-attributable at a glance.
  const kpiUnknowns = useMemo(
    () => data?.nodes.filter((n) => n.label === "unknown").length ?? 0,
    [data],
  );

  // Unattributed nodes table — for each "unknown" node compute the total
  // request count (sum of incoming links). Drives a small "Investigate"
  // link that opens the existing layer-appropriate flow drill-down so
  // operators have one click from "I see an unknown" → "here's what's
  // contributing to it".
  const unknownRows = useMemo(() => {
    if (!data) return [];
    const out: Array<{ id: string; layer: TopologyLayer; count: number }> = [];
    for (const n of data.nodes) {
      if (n.label !== "unknown") continue;
      const incoming = data.links
        .filter((l) => l.target === n.id)
        .reduce((acc, l) => acc + l.value, 0);
      out.push({ id: n.id, layer: n.layer, count: incoming });
    }
    return out.sort((a, b) => b.count - a.count);
  }, [data]);

  const providerData = useMemo(() => {
    if (!stats?.upstreams) return [];
    return Object.entries(stats.upstreams)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 6)
      .map(([k, v]) => ({
        name: rewriteLabel(k, "upstream"),
        value: v,
      }));
  }, [stats]);

  return (
    <>
      <PageHeader
        title="Network Map"
        description="Agent traffic flow from network segments through the KYDE Gateway to AI providers and models."
        lastUpdated={dataUpdatedAt}
        actions={
          <Select value={window} onValueChange={(v) => setWindow(v as TopologyWindow)}>
            <SelectTrigger className="h-9 w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1h">Last 1 hour</SelectItem>
              <SelectItem value="24h">Last 24 hours</SelectItem>
              <SelectItem value="7d">Last 7 days</SelectItem>
              <SelectItem value="30d">Last 30 days</SelectItem>
            </SelectContent>
          </Select>
        }
      />

      {/* KPI block */}
      <div className="grid grid-cols-5 gap-3 mb-7">
        <MetricCard label="Total Nodes" value={kpiNodes} />
        <MetricCard label="Network Segments" value={kpiAgents} />
        <MetricCard label="AI Providers" value={kpiProviders} />
        <MetricCard label="Models" value={kpiModels} />
        <MetricCard
          label="Unknowns"
          value={kpiUnknowns}
          subtext={kpiUnknowns > 0 ? "unattributed nodes" : undefined}
        />
      </div>

      {/* Sankey chart */}
      <ChartCard title="Network segment → agent → KYDE Gateway → AI provider → model" height={520}>
        {isLoading || !sankey ? (
          <Skeleton className="h-full w-full" />
        ) : sankey.links.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No traffic in the selected window.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <Sankey
              data={sankey}
              nodePadding={18}
              nodeWidth={10}
              linkCurvature={0.5}
              iterations={64}
              margin={{ top: 10, right: 140, bottom: 10, left: 140 }}
              link={(linkProps: {
                sourceX: number; sourceY: number; sourceControlX: number;
                targetX: number; targetY: number; targetControlX: number;
                linkWidth: number;
                payload: { source: { name: string }; target: { name: string }; value: number };
                index: number;
              }) => {
                // Recharts gives us node refs by index — look up layer +
                // label from the original sankey data so the click opens
                // the right flow detail.
                const sLink = sankey.links[linkProps.index];
                const srcNode = sankey.nodes[sLink.source];
                const tgtNode = sankey.nodes[sLink.target];
                const path = `M${linkProps.sourceX},${linkProps.sourceY}` +
                  `C${linkProps.sourceControlX},${linkProps.sourceY}` +
                  ` ${linkProps.targetControlX},${linkProps.targetY}` +
                  ` ${linkProps.targetX},${linkProps.targetY}`;
                return (
                  <path
                    d={path}
                    stroke={tgtNode.layer === "gateway" ? colorForNode(srcNode) : colorForNode(tgtNode)}
                    strokeWidth={linkProps.linkWidth}
                    strokeOpacity={0.45}
                    fill="none"
                    style={{ cursor: "pointer" }}
                    onMouseEnter={(e) => { (e.currentTarget as SVGPathElement).setAttribute("stroke-opacity", "0.72"); }}
                    onMouseLeave={(e) => { (e.currentTarget as SVGPathElement).setAttribute("stroke-opacity", "0.45"); }}
                    onClick={() => {
                      setSelectedFlow({
                        source: { layer: srcNode.layer, label: data!.nodes.find((n) => n.id === srcNode.id)!.label },
                        target: { layer: tgtNode.layer, label: data!.nodes.find((n) => n.id === tgtNode.id)!.label },
                      });
                    }}
                  >
                    <title>{`${srcNode.name} → ${tgtNode.name}: ${linkProps.payload.value}`}</title>
                  </path>
                );
              }}
              node={(props) => (
                <NetworkNode {...(props as unknown as Parameters<typeof NetworkNode>[0])} />
              )}
            >
              <Tooltip contentStyle={TOOLTIP_STYLE} />
            </Sankey>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Legend — flows are colored by destination model (ref network-flow.png). */}
      {modelLegend.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-2 pb-4 text-xs">
          <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">flows by model:</span>
          {modelLegend.map((m) => (
            <span key={m.name} className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-sm" style={{ background: m.color }} />
              <span className="font-mono">{m.name}</span>
            </span>
          ))}
        </div>
      )}

      {/* Unattributed (unknown) nodes — small table with an Investigate
          jump-link per row. The flow side panel handles the actual
          drill-down once the user clicks. */}
      {unknownRows.length > 0 && (
        <div className="mt-4 rounded-md border bg-card p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="text-sm font-semibold">Unattributed nodes</h3>
            <span className="text-[11px] text-muted-foreground">
              {unknownRows.length} layer{unknownRows.length === 1 ? "" : "s"} need labels
            </span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="py-2 text-left font-medium">Layer</th>
                <th className="py-2 text-right font-medium">Requests</th>
                <th className="py-2 text-right font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {unknownRows.map((u) => {
                // Pick the most informative incoming link to drive the
                // Investigate click. Default to the first one for now;
                // sorting by count is overkill for the panel to populate.
                const incoming = data!.links.filter((l) => l.target === u.id);
                const first = incoming[0];
                return (
                  <tr key={u.id} className="border-b last:border-0">
                    <td className="py-2 font-mono text-xs">{u.layer}</td>
                    <td className="py-2 text-right font-mono text-xs">{u.count.toLocaleString()}</td>
                    <td className="py-2 text-right">
                      {first ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            const src = data!.nodes.find((n) => n.id === first.source);
                            const tgt = data!.nodes.find((n) => n.id === first.target);
                            if (!src || !tgt) return;
                            setSelectedFlow({
                              source: { layer: src.layer, label: src.label },
                              target: { layer: tgt.layer, label: tgt.label },
                            });
                          }}
                        >
                          Investigate →
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* AI Provider Distribution */}
      {providerData.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 mt-4">
          <ChartCard title="AI Provider Distribution">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={providerData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  labelLine={false}
                >
                  {providerData.map((_, i) => (
                    <Cell key={i} fill={STREAM_PALETTE[i % STREAM_PALETTE.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>

          <div className="rounded-lg border bg-card p-5">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Provider Request Counts</p>
            <div className="space-y-2">
              {providerData.map((p, i) => {
                const total = providerData.reduce((s, d) => s + d.value, 0);
                const pct = total ? (p.value / total) * 100 : 0;
                return (
                  <div key={i} className="space-y-1">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium">{p.name}</span>
                      <span className="font-mono text-xs text-muted-foreground">{p.value.toLocaleString()}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{ width: `${pct}%`, background: STREAM_PALETTE[i % STREAM_PALETTE.length] }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <Sheet open={selectedFlow !== null} onOpenChange={(o) => !o && setSelectedFlow(null)}>
        <SheetContent className="w-[420px] sm:max-w-[420px] overflow-y-auto">
          {selectedFlow && (
            <>
              <SheetHeader className="mb-4">
                <SheetTitle className="text-base">
                  {rewriteLabel(selectedFlow.source.label, selectedFlow.source.layer)}
                  <span className="mx-2 text-muted-foreground">→</span>
                  {rewriteLabel(selectedFlow.target.label, selectedFlow.target.layer)}
                </SheetTitle>
              </SheetHeader>
              {flowLoading || !flowData ? (
                <div className="space-y-2">
                  <div className="h-6 animate-pulse rounded bg-muted" />
                  <div className="h-24 animate-pulse rounded bg-muted" />
                </div>
              ) : (
                <div className="space-y-5 text-sm">
                  <div className="flex items-baseline justify-between">
                    <span className="text-muted-foreground">Requests</span>
                    <span className="font-mono text-lg font-semibold">
                      {flowData.request_count.toLocaleString()}
                    </span>
                  </div>
                  {flowData.first_seen_iso && (
                    <div className="text-[11px] text-muted-foreground">
                      {flowData.first_seen_iso.slice(0, 19).replace("T", " ")} —{" "}
                      {flowData.last_seen_iso?.slice(0, 19).replace("T", " ")}
                    </div>
                  )}

                  <section>
                    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Top agents
                    </h3>
                    {flowData.agents.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No agents in this flow.</p>
                    ) : (
                      <ul className="space-y-1">
                        {flowData.agents.map((a) => (
                          <li key={a.agent_id} className="flex items-center justify-between gap-2">
                            <NavLink
                              to={`/agents/${encodeURIComponent(a.agent_id)}`}
                              className="font-mono text-xs truncate text-primary hover:underline"
                              onClick={() => setSelectedFlow(null)}
                              title={`Open agent ${a.agent_id}`}
                            >
                              {a.display_name ?? a.agent_id}
                            </NavLink>
                            <span className="font-mono text-xs text-muted-foreground shrink-0">
                              {a.request_count}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  <section>
                    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Recent sessions
                    </h3>
                    {flowData.sessions.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No sessions in this flow.</p>
                    ) : (
                      <ul className="space-y-1">
                        {flowData.sessions.map((s) => (
                          <li key={s.session_id} className="flex items-center justify-between gap-2">
                            <NavLink
                              to={`/sessions/${s.session_id}`}
                              className="font-mono text-xs hover:underline truncate"
                              title={s.session_id}
                            >
                              {s.serial_id !== null ? formatSessionId(s.serial_id) : "SES-?"}
                            </NavLink>
                            <span className="font-mono text-xs text-muted-foreground shrink-0">
                              {s.request_count}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  <Button variant="outline" size="sm" onClick={() => setSelectedFlow(null)}>
                    Close
                  </Button>
                </div>
              )}
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
