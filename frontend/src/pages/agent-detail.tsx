import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Pencil, Save, X } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { CopyButton } from "@/components/shared/copy-button";
import { TrafficInventory } from "@/components/shared/traffic-inventory";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAgents,
  useBlockAgent,
  useFleetTrust,
  useTokenAnalysis,
  useTopologyAgent,
  useUnblockAgent,
  useUpdateAgent,
  type StatsWindow,
} from "@/api/queries";
import { TrustScoreHero } from "@/components/shared/trust-score";
import type { TopologyWindow } from "@/api/types";
import { useMe } from "@/hooks/use-me";
import { useFeatures } from "@/hooks/use-features";
import { EnterpriseLock } from "@/components/shared/upgrade-lock";
import { getAgentDisplayName } from "@/lib/agent-names";
import { fmtTokens } from "@/lib/format";
import { formatHost } from "@/lib/host-format";
import { cn } from "@/lib/utils";

// Deep-linkable entity page for one agent_id. Mirrors the data shape of
// the Agent Detail modal but adds the per-(provider, model) cost block —
// the modal omits cost to keep its surface small.
//
// Routing: /agents/:agent_id (RequireAuditor — admin or auditor).
// 30-day window is the topology endpoint's most forensic-friendly value
// without paying for a true full scan.

const ACTIVE_WINDOW_MS = 24 * 60 * 60 * 1000;

function isActive(lastSeen: string | null | undefined) {
  if (!lastSeen) return false;
  const t = new Date(lastSeen).getTime();
  if (!Number.isFinite(t)) return false;
  return Date.now() - t < ACTIVE_WINDOW_MS;
}

export default function AgentDetailPage() {
  const { agentId: agentIdParam } = useParams<{ agentId: string }>();
  const agentId = agentIdParam ?? "";

  const { data: topology, isLoading: topologyLoading, isError, error } =
    useTopologyAgent(agentId, "30d" as TopologyWindow);
  const { data: roster = [] } = useAgents();
  // Token analysis scoped to this agent — drives the token KPIs + per-model
  // token breakdown.
  const { data: tokens, isLoading: tokensLoading } = useTokenAnalysis(
    "30d" as StatsWindow,
    agentId,
  );
  // Per-agent trust over the same 30d window as the rest of this page.
  const { data: trust } = useFleetTrust("30d" as StatsWindow);
  const agentTrust = useMemo(
    () => trust?.agents.find((a) => a.agent_id === agentId),
    [trust, agentId],
  );

  const rosterEntry = useMemo(
    () => roster.find((a) => a.agent_id === agentId),
    [roster, agentId],
  );

  const { isAdmin, me } = useMe();
  const { enforcementEnabled } = useFeatures();
  const blockAgent = useBlockAgent();
  const unblockAgent = useUnblockAgent();

  const onBlock = async () => {
    if (!window.confirm(
      `Block ${agentId}? All future proxy requests from this agent will be rejected with 403.`,
    )) return;
    try {
      await blockAgent.mutateAsync({
        agent_id: agentId,
        reason: `Blocked from Agent detail by ${me?.username ?? "admin"}`,
      });
      toast.success(`Agent ${agentId} blocked`);
    } catch (err) {
      toast.error((err as Error).message || "Block failed");
    }
  };

  const onUnblock = async () => {
    try {
      await unblockAgent.mutateAsync(agentId);
      toast.success(`Agent ${agentId} unblocked`);
    } catch (err) {
      toast.error((err as Error).message || "Unblock failed");
    }
  };

  const displayName = rosterEntry?.display_name ?? null;
  const active = isActive(topology?.last_seen_iso ?? rosterEntry?.last_seen_dt ?? null);

  // Inline rename — admin only. The agents query invalidation done by
  // useUpdateAgent refreshes `displayName` and the page title with it.
  const updateAgent = useUpdateAgent();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName ?? "");
  useEffect(() => {
    if (!editing) setDraft(displayName ?? "");
  }, [displayName, editing]);

  const fallbackName = getAgentDisplayName(agentId);
  const onSaveName = async () => {
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : trimmed;
    if (next === displayName) {
      setEditing(false);
      return;
    }
    try {
      await updateAgent.mutateAsync({ agent_id: agentId, display_name: next });
      toast.success(next ? "Agent name saved" : "Agent name cleared");
      setEditing(false);
    } catch (err) {
      toast.error((err as Error).message || "Save failed");
    }
  };

  if (isError) {
    return (
      <>
        <PageHeader title="Agent" />
        <div className="rounded-md border bg-card p-8 text-center text-sm text-muted-foreground">
          <p className="font-semibold text-foreground mb-1">Failed to load agent.</p>
          <p>{(error as Error)?.message ?? "Unknown error"}</p>
          <Link to="/agent-activity" className="text-primary hover:underline mt-3 inline-block">
            ← Back to Agent Activity
          </Link>
        </div>
      </>
    );
  }

  if (topologyLoading || !topology) {
    return (
      <>
        <PageHeader title="Agent" />
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </>
    );
  }

  // Per-(provider, model) cost. tokens.by_model is keyed by model name and
  // already includes EUR + USD per model. We don't have provider+model
  // breakdown server-side; show by_model and by_upstream side-by-side.
  const totalRequests = topology.request_count;
  const totalTokens =
    (tokens?.total_prompt_tokens ?? 0) + (tokens?.total_completion_tokens ?? 0);

  return (
    <>
      <PageHeader
        title={displayName ?? fallbackName}
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-muted-foreground">{agentId}</span>
            <CopyButton value={agentId} label="agent id" />
            <span
              className={cn(
                "ml-2 inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-medium",
                active ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
              )}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", active ? "bg-primary" : "bg-muted-foreground")} />
              {active ? "active (last 24h)" : "idle"}
            </span>
            {isAdmin && !editing && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
                onClick={() => setEditing(true)}
              >
                <Pencil className="mr-1 h-3 w-3" />
                {displayName ? "Rename" : "Name agent"}
              </Button>
            )}
          </span>
        }
        actions={
          <Link
            to="/agent-activity"
            className="text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            ← Agent Activity
          </Link>
        }
      />

      {/* Inline rename row — admin only, surfaces when editing. Empty
          input clears the name and the title falls back to the
          hash-derived label. */}
      {isAdmin && editing && (
        <div className="mb-7 flex flex-wrap items-center gap-2 rounded-md border border-border bg-card p-3">
          <Input
            value={draft}
            placeholder={fallbackName}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSaveName();
              if (e.key === "Escape") setEditing(false);
            }}
            autoFocus
            className="max-w-md text-sm"
          />
          <Button size="sm" onClick={onSaveName} disabled={updateAgent.isPending}>
            <Save className="mr-1 h-3 w-3" />
            Save
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setEditing(false)}
            disabled={updateAgent.isPending}
          >
            <X className="mr-1 h-3 w-3" />
            Cancel
          </Button>
          <span className="text-[11px] text-muted-foreground">
            Empty = use the hash-derived default ({fallbackName}).
          </span>
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        <MetricCard label="Requests (30d)" value={totalRequests.toLocaleString()} />
        <MetricCard label="Sessions" value={rosterEntry?.session_count?.toLocaleString() ?? topology.sessions.length} />
        <MetricCard label="Tokens (30d)" value={fmtTokens(totalTokens)} />
        <MetricCard
          label="Prompt / Completion"
          value={
            fmtTokens(tokens?.total_prompt_tokens ?? 0) +
            " / " +
            fmtTokens(tokens?.total_completion_tokens ?? 0)
          }
        />
      </div>

      {/* Agent trust — compact composite with the labeled gauge and the 5
          dimension scales that feed it. */}
      {agentTrust ? (
        <div className="mb-7">
          <TrustScoreHero
            score={agentTrust.score}
            tierKey={agentTrust.tier_key}
            tier={agentTrust.tier}
            label="Agent Trust Score"
            caption={`${agentTrust.requests.toLocaleString()} requests (30d)`}
            dimensions={agentTrust.dimensions}
            compact
          />
        </div>
      ) : null}

      {/* Tools / Upstreams / Models. The /api/topology/agent breakdowns
          use dynamic label keys ("tool", "upstream", "model") + a shared
          request_count, so we project them into a uniform shape here. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 mb-7">
        <BreakdownCard title="Tools" items={breakdownItems(topology.tools, "tool")} />
        <BreakdownCard title="AI providers" items={breakdownItems(topology.upstreams, "upstream")} />
        <BreakdownCard title="Models used" items={breakdownItems(topology.models, "model")} />
      </div>

      {/* Token breakdown by model */}
      {tokens && (tokens.total_tokens ?? 0) > 0 && (
        <section className="rounded-md border bg-card p-5 mb-7">
          <h2 className="text-sm font-semibold mb-3">Tokens by model (30d)</h2>
          {tokensLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="py-2 text-left font-medium">Model</th>
                  <th className="py-2 text-right font-medium">Prompt</th>
                  <th className="py-2 text-right font-medium">Completion</th>
                  <th className="py-2 text-right font-medium">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(tokens.by_model)
                  .map(([model, bucket]) => ({
                    model,
                    prompt: bucket.prompt_tokens ?? 0,
                    completion: bucket.completion_tokens ?? 0,
                    total: (bucket.prompt_tokens ?? 0) + (bucket.completion_tokens ?? 0),
                  }))
                  .sort((a, b) => b.total - a.total)
                  .map((row) => (
                    <tr key={row.model} className="border-b last:border-0">
                      <td className="py-2 font-mono text-xs">{row.model}</td>
                      <td className="py-2 text-right font-mono text-xs">{fmtTokens(row.prompt)}</td>
                      <td className="py-2 text-right font-mono text-xs">{fmtTokens(row.completion)}</td>
                      <td className="py-2 text-right font-mono text-xs font-semibold">{fmtTokens(row.total)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {/* Segments observed */}
      {topology.segments.length > 0 && (
        <section className="rounded-md border bg-card p-5 mb-7">
          <h2 className="text-sm font-semibold mb-3">Segments observed</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="py-2 text-left font-medium">Segment</th>
                <th className="py-2 text-left font-medium">Class</th>
                <th className="py-2 text-right font-medium">Requests</th>
              </tr>
            </thead>
            <tbody>
              {topology.segments.map((s) => (
                <tr key={s.subnet} className="border-b last:border-0">
                  <td className="py-2 font-mono text-xs">{s.subnet}</td>
                  <td className="py-2 font-mono text-xs text-muted-foreground">{s.class}</td>
                  <td className="py-2 text-right font-mono text-xs">{s.request_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* IPs observed → /hosts links. Hostname surfaced inline as
          "hostname (ip)" when known; bare IP otherwise. */}
      {topology.ips.length > 0 && (
        <section className="rounded-md border bg-card p-5 mb-7">
          <h2 className="text-sm font-semibold mb-3">Hosts observed</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="py-2 text-left font-medium">Host</th>
                <th className="py-2 text-right font-medium">Requests</th>
              </tr>
            </thead>
            <tbody>
              {topology.ips.slice(0, 10).map((ip) => (
                <tr key={ip.ip} className="border-b last:border-0">
                  <td className="py-2 font-mono text-xs">
                    <Link
                      to={`/hosts/${encodeURIComponent(ip.ip)}`}
                      className="text-primary hover:underline"
                      title={ip.hostname ? `${ip.hostname} (${ip.ip})` : ip.ip}
                    >
                      {formatHost(ip.ip, ip.hostname)}
                    </Link>
                  </td>
                  <td className="py-2 text-right font-mono text-xs">{ip.request_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Recent sessions */}
      <section className="rounded-md border bg-card p-5 mb-7">
        <h2 className="text-sm font-semibold mb-3">Recent sessions</h2>
        {topology.sessions.length === 0 ? (
          <p className="text-xs text-muted-foreground">No sessions in the last 30 days.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="py-2 text-left font-medium">Session</th>
                <th className="py-2 text-left font-medium">Last seen</th>
                <th className="py-2 text-right font-medium">Entries</th>
              </tr>
            </thead>
            <tbody>
              {topology.sessions.slice(0, 20).map((s) => (
                <tr key={s.session_id} className="border-b last:border-0">
                  <td className="py-2 font-mono text-xs">
                    <Link to={`/sessions/${s.session_id}`} className="text-primary hover:underline">
                      {s.session_id}
                    </Link>
                  </td>
                  <td className="py-2 font-mono text-[11px] text-muted-foreground">
                    {s.last_seen_iso?.slice(0, 19).replace("T", " ")}
                  </td>
                  <td className="py-2 text-right font-mono text-xs">{s.request_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <TrafficInventory agentId={agentId} isAdmin={isAdmin} />

      {/* Admin actions */}
      {isAdmin && (
        <section className="rounded-md border bg-card p-5">
          <h2 className="text-sm font-semibold mb-3">Admin actions</h2>
          <div className="flex flex-wrap gap-2">
            <EnterpriseLock
              locked={!enforcementEnabled}
              hint="Agent blocking is part of enforcement — available in the KYDE Enterprise edition. The starter edition is observe-only."
            >
              <Button
                variant="outline"
                className="border-destructive text-destructive hover:bg-destructive/10"
                onClick={onBlock}
                disabled={blockAgent.isPending}
              >
                Block agent
              </Button>
            </EnterpriseLock>
            <EnterpriseLock
              locked={!enforcementEnabled}
              hint="Agent blocking is part of enforcement — available in the KYDE Enterprise edition. The starter edition is observe-only."
            >
              <Button
                variant="outline"
                onClick={onUnblock}
                disabled={unblockAgent.isPending}
              >
                Unblock agent
              </Button>
            </EnterpriseLock>
            <Link
              to={`/audit-log?agent=${encodeURIComponent(agentId)}`}
              className="text-xs text-muted-foreground hover:text-foreground hover:underline self-center ml-2"
            >
              View entries in Audit Log →
            </Link>
            <Link
              to={`/agent-chains?agent=${encodeURIComponent(agentId)}`}
              className="text-xs text-muted-foreground hover:text-foreground hover:underline self-center"
            >
              View chains →
            </Link>
          </div>
        </section>
      )}
    </>
  );
}

// Project a CountBreakdown row (dynamic-label shape) into {label, value}.
function breakdownItems(
  rows: Array<{ request_count: number; [k: string]: number | string }>,
  labelKey: string,
): Array<{ label: string; value: number }> {
  return rows.slice(0, 10).map((r) => ({
    label: String(r[labelKey] ?? "unknown"),
    value: Number(r.request_count) || 0,
  }));
}

function BreakdownCard({
  title,
  items,
}: {
  title: string;
  items: Array<{ label: string; value: number }>;
}) {
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">None.</p>
      ) : (
        <ul className="space-y-1">
          {items.map((it) => (
            <li key={it.label} className="flex justify-between gap-2">
              <span className="font-mono text-xs truncate">{it.label}</span>
              <span className="font-mono text-xs text-muted-foreground">{it.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
