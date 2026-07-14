import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Pencil, Save, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { CopyButton } from "@/components/shared/copy-button";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useDeleteHostLabel,
  useHostResolve,
  useTopologyIp,
  useUpsertHostLabel,
} from "@/api/queries";
import type { OriginClass, TopologyWindow } from "@/api/types";
import { cn } from "@/lib/utils";

// Deep-linkable entity page for one host. Phase 1 accepts an IP only;
// the URL signature uses :identifier so a future Phase 2 can wire
// hostname resolution without breaking the route contract. Hostnames
// 404 cleanly today.
//
// IP detection: very loose — anything containing ":" (IPv6) or matching
// the dotted-quad pattern. A real validator would use ipaddress.ip_address
// but for the URL gate that's overkill — a malformed string just hits the
// "host resolution not yet wired" empty state.

const CLASS_LABEL: Record<OriginClass, string> = {
  public: "Public",
  rfc1918: "RFC1918 (private)",
  cgnat: "CGNAT",
  loopback: "Loopback",
  link_local: "Link-local",
  unique_local_v6: "IPv6 ULA",
  unknown: "Unknown",
};

const CLASS_STYLE: Record<OriginClass, string> = {
  public:          "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
  rfc1918:         "bg-primary/10 text-primary border-primary/20",
  cgnat:           "bg-purple-100 text-purple-700 border-purple-200",
  loopback:        "bg-muted text-muted-foreground border-border",
  link_local:      "bg-muted text-muted-foreground border-border",
  unique_local_v6: "bg-primary/10 text-primary border-primary/20",
  unknown:         "bg-muted text-muted-foreground border-border",
};

function looksLikeIP(s: string): boolean {
  // Quick gate — IPv4 dotted-quad or IPv6 (contains ":"). Doesn't enforce
  // octet ranges; that's the backend's job.
  return /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/.test(s) || s.includes(":");
}

function breakdownItems(
  rows: Array<{ request_count: number; [k: string]: number | string }>,
  labelKey: string,
): Array<{ label: string; value: number }> {
  return rows.slice(0, 10).map((r) => ({
    label: String(r[labelKey] ?? "unknown"),
    value: Number(r.request_count) || 0,
  }));
}

export default function HostDetailPage() {
  const { identifier: idParam } = useParams<{ identifier: string }>();
  const identifier = idParam ?? "";
  const isIp = looksLikeIP(identifier);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const upsertLabel = useUpsertHostLabel();
  const deleteLabel = useDeleteHostLabel();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  // IP branch: hit topology/ip directly — the endpoint also triggers the
  // lazy resolver so the response carries the hostname.
  const ipQuery = useTopologyIp(
    isIp ? identifier : null,
    "30d" as TopologyWindow,
  );

  // Hostname branch: ask the resolver for matching IPs and either
  // auto-redirect (single match) or render a picker (multiple).
  const hostnameQuery = useHostResolve(!isIp ? identifier : null);

  // Single-match hostname → replace the URL with the IP so the user
  // lands on the canonical /hosts/<ip> path. useEffect because react-
  // router's navigate() can't be called during render.
  useEffect(() => {
    if (!isIp && hostnameQuery.data?.kind === "hostname") {
      const ips = hostnameQuery.data.ips;
      if (ips.length === 1) {
        navigate(`/hosts/${encodeURIComponent(ips[0].ip)}`, { replace: true });
      }
    }
  }, [isIp, hostnameQuery.data, navigate]);

  const data = ipQuery.data;
  const isLoading = isIp ? ipQuery.isLoading : hostnameQuery.isLoading;
  const isError = isIp ? ipQuery.isError : hostnameQuery.isError;

  const breakdowns = useMemo(() => {
    if (!data) return null;
    return {
      tools: breakdownItems(data.tools, "tool"),
      upstreams: breakdownItems(data.upstreams, "upstream"),
      models: breakdownItems(data.models, "model"),
    };
  }, [data]);

  const onSaveName = async () => {
    const value = draft.trim();
    if (!value) {
      toast.error("Host name is required");
      return;
    }
    try {
      await upsertLabel.mutateAsync({ ip: identifier, hostname: value });
      await qc.invalidateQueries({ queryKey: ["topology-ip"] });
      await qc.invalidateQueries({ queryKey: ["topology"] });
      toast.success(`Name set for ${identifier}`);
      setEditing(false);
    } catch (err) {
      toast.error((err as Error).message || "Save failed");
    }
  };

  const onClearName = async () => {
    try {
      await deleteLabel.mutateAsync(identifier);
      await qc.invalidateQueries({ queryKey: ["topology-ip"] });
      await qc.invalidateQueries({ queryKey: ["topology"] });
      toast.success(`Cleared name for ${identifier}`);
      setEditing(false);
    } catch (err) {
      toast.error((err as Error).message || "Clear failed");
    }
  };

  // Hostname identifier: render the picker or the empty state. The
  // single-match case has already been auto-redirected by the effect
  // above by the time we reach this render.
  if (!isIp) {
    if (hostnameQuery.isLoading) {
      return (
        <>
          <PageHeader title="Host" />
          <Skeleton className="h-24 w-full" />
        </>
      );
    }
    const hostnameData =
      hostnameQuery.data?.kind === "hostname" ? hostnameQuery.data : null;
    const ips = hostnameData?.ips ?? [];
    if (ips.length === 0) {
      return (
        <>
          <PageHeader title="Host" />
          <div className="rounded-md border bg-card p-8 text-center text-sm">
            <p className="font-semibold mb-1">Unknown hostname.</p>
            <p className="text-muted-foreground">
              <code className="font-mono">{identifier}</code> doesn't match any IP we've
              labeled or resolved. Set an admin label in{" "}
              <Link to="/settings" className="text-primary hover:underline">Settings → Host names</Link>{" "}
              or try a different identifier.
            </p>
          </div>
        </>
      );
    }
    // Multiple matches → picker. One match is impossible here (auto-redirect).
    return (
      <>
        <PageHeader
          title={identifier}
          description={
            <span className="font-mono text-xs text-muted-foreground">
              hostname resolves to {ips.length} IPs · most recent first
            </span>
          }
        />
        <div className="rounded-md border bg-card divide-y">
          {ips.map((row) => (
            <Link
              key={row.ip}
              to={`/hosts/${encodeURIComponent(row.ip)}`}
              className="flex items-center justify-between px-4 py-3 text-sm hover:bg-accent/40"
            >
              <span className="font-mono">{row.ip}</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {row.source === "admin" ? "labeled" : "dns"}
                {row.last_seen
                  ? ` · seen ${new Date(row.last_seen * 1000).toISOString().slice(0, 19).replace("T", " ")}`
                  : " · no traffic"}
              </span>
            </Link>
          ))}
        </div>
      </>
    );
  }

  if (isError) {
    return (
      <>
        <PageHeader title="Host" />
        <div className="rounded-md border bg-card p-8 text-center text-sm text-muted-foreground">
          <p className="font-semibold text-foreground mb-1">Failed to load host detail.</p>
          <Link to="/network-map" className="text-primary hover:underline mt-3 inline-block">
            ← Back to Network Map
          </Link>
        </div>
      </>
    );
  }

  if (isLoading || !data) {
    return (
      <>
        <PageHeader title="Host" />
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </>
    );
  }

  const cls = data.class as OriginClass;
  const totalAgents = data.agents.length;

  return (
    <>
      <PageHeader
        title={data.hostname ?? identifier}
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-muted-foreground">
              {data.hostname ? `${identifier}` : `IP ${identifier}`}
            </span>
            <CopyButton value={identifier} label="ip" />
            {data.hostname && (
              <span
                className={cn(
                  "inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide",
                  data.hostname_source === "admin"
                    ? "bg-primary/10 text-primary border-primary/20"
                    : "bg-muted text-muted-foreground border-border",
                )}
                title={
                  data.hostname_source === "admin"
                    ? "Hostname set by an admin label"
                    : "Hostname from reverse DNS"
                }
              >
                {data.hostname_source === "admin" ? "labeled" : "dns"}
              </span>
            )}
            <span className={cn("inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium", CLASS_STYLE[cls])}>
              {CLASS_LABEL[cls]}
            </span>
            {data.subnet && (
              <span className="font-mono text-[11px] text-muted-foreground">
                · subnet {data.subnet}
              </span>
            )}
          </span>
        }
        actions={
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setDraft(data.hostname_source === "admin" ? data.hostname ?? "" : "");
                setEditing(true);
              }}
            >
              <Pencil className="mr-1 h-3 w-3" />
              {data.hostname_source === "admin" ? "Edit name" : "Set name"}
            </Button>
            <Link
              to="/network-map"
              className="text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              ← Network Map
            </Link>
          </>
        }
      />

      {editing && (
        <div className="mb-7 rounded-md border bg-card p-4">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Host name for <span className="font-mono normal-case">{identifier}</span>
          </div>
          <div className="flex items-center gap-2">
            <Input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="crm.internal"
              className="h-8 max-w-sm text-xs font-mono"
              onKeyDown={(e) => {
                if (e.key === "Enter") onSaveName();
                if (e.key === "Escape") setEditing(false);
              }}
            />
            <Button size="sm" onClick={onSaveName} disabled={upsertLabel.isPending}>
              <Save className="mr-1 h-3 w-3" /> Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            {data.hostname_source === "admin" && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto text-destructive hover:text-destructive"
                onClick={onClearName}
                disabled={deleteLabel.isPending}
                title="Remove admin label (falls back to reverse DNS)"
              >
                <Trash2 className="mr-1 h-3 w-3" /> Clear label
              </Button>
            )}
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Admin labels override reverse DNS for this IP across the dashboard.
          </p>
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        <MetricCard label="Requests (30d)" value={data.request_count.toLocaleString()} />
        <MetricCard label="Agents seen" value={totalAgents} />
        <MetricCard
          label="First seen"
          small
          value={data.first_seen_iso ? data.first_seen_iso.slice(0, 10) : "—"}
        />
        <MetricCard
          label="Last seen"
          small
          value={data.last_seen_iso ? data.last_seen_iso.slice(0, 19).replace("T", " ") : "—"}
        />
      </div>

      {data.request_count === 0 ? (
        <div className="rounded-md border bg-card p-12 text-center text-sm text-muted-foreground">
          No traffic observed from this IP in the last 30 days.
        </div>
      ) : (
        <>
          {/* Agents observed → /agents links */}
          <section className="rounded-md border bg-card p-5 mb-7">
            <h2 className="text-sm font-semibold mb-3">Agents observed</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="py-2 text-left font-medium">Agent</th>
                  <th className="py-2 text-left font-medium">Tools</th>
                  <th className="py-2 text-left font-medium">Last seen</th>
                  <th className="py-2 text-right font-medium">Requests</th>
                </tr>
              </thead>
              <tbody>
                {data.agents.map((a) => (
                  <tr key={a.agent_id} className="border-b last:border-0">
                    <td className="py-2 font-mono text-xs">
                      <Link
                        to={`/agents/${encodeURIComponent(a.agent_id)}`}
                        className="text-primary hover:underline"
                      >
                        {a.agent_id}
                      </Link>
                    </td>
                    <td className="py-2 font-mono text-[11px] text-muted-foreground">
                      {a.tools.join(", ")}
                    </td>
                    <td className="py-2 font-mono text-[11px] text-muted-foreground">
                      {a.last_seen_iso?.slice(0, 19).replace("T", " ")}
                    </td>
                    <td className="py-2 text-right font-mono text-xs">{a.request_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* Tools / Upstreams / Models */}
          {breakdowns && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 mb-7">
              <BreakdownCard title="Tools"        items={breakdowns.tools} />
              <BreakdownCard title="AI providers" items={breakdowns.upstreams} />
              <BreakdownCard title="Models used"  items={breakdowns.models} />
            </div>
          )}

          {/* Recent sessions */}
          <section className="rounded-md border bg-card p-5">
            <h2 className="text-sm font-semibold mb-3">Recent sessions</h2>
            {data.sessions.length === 0 ? (
              <p className="text-xs text-muted-foreground">No sessions in the last 30 days.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="py-2 text-left font-medium">Session</th>
                    <th className="py-2 text-left font-medium">Model</th>
                    <th className="py-2 text-left font-medium">Last seen</th>
                    <th className="py-2 text-right font-medium">Entries</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sessions.slice(0, 20).map((s) => (
                    <tr key={s.session_id} className="border-b last:border-0">
                      <td className="py-2 font-mono text-xs">
                        <Link to={`/sessions/${s.session_id}`} className="text-primary hover:underline">
                          {s.session_id}
                        </Link>
                      </td>
                      <td className="py-2 font-mono text-[11px] text-muted-foreground">{s.model}</td>
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
        </>
      )}
    </>
  );
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
