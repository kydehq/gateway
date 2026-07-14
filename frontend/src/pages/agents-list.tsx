import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAgents, useFleetTrust } from "@/api/queries";
import type { Agent, AgentTrust } from "@/api/types";
import { TrustBadge } from "@/components/shared/trust-score";
import { cn } from "@/lib/utils";

// Browse-all-agents list. Distinct from Agent Activity (forensics-shaped:
// charts + per-agent rollups for the chosen window) — this is a plain
// roster: every known agent, sortable, searchable, click-through to the
// per-agent detail page.

type SortKey = "label" | "trust" | "sessions" | "entries" | "last_seen" | "first_seen";

const ACTIVE_WINDOW_MS = 24 * 60 * 60 * 1000;

function isActive(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return false;
  const t = new Date(lastSeen).getTime();
  if (!Number.isFinite(t)) return false;
  return Date.now() - t < ACTIVE_WINDOW_MS;
}

function labelOf(a: Agent): string {
  return a.display_name && a.display_name.trim() !== "" ? a.display_name : a.agent_id;
}

export default function AgentsListPage() {
  const { data: agents = [], isLoading } = useAgents();
  // "all" so every agent in the roster has a score, not just last-24h actives.
  const { data: trust } = useFleetTrust("all");
  const trustById = useMemo(() => {
    const m = new Map<string, AgentTrust>();
    for (const a of trust?.agents ?? []) m.set(a.agent_id, a);
    return m;
  }, [trust]);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("last_seen");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) =>
        a.agent_id.toLowerCase().includes(q) ||
        (a.display_name?.toLowerCase().includes(q) ?? false),
    );
  }, [agents, search]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: number | string;
      let bv: number | string;
      switch (sortKey) {
        case "label":      av = labelOf(a).toLowerCase(); bv = labelOf(b).toLowerCase(); break;
        case "trust":     av = trustById.get(a.agent_id)?.score ?? -1; bv = trustById.get(b.agent_id)?.score ?? -1; break;
        case "sessions":   av = a.session_count;          bv = b.session_count; break;
        case "entries":    av = a.entry_count;            bv = b.entry_count; break;
        case "first_seen": av = a.first_seen;             bv = b.first_seen; break;
        case "last_seen":
        default:           av = a.last_seen;              bv = b.last_seen; break;
      }
      if (av === bv) return 0;
      const cmp = av < bv ? -1 : 1;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir, trustById]);

  const activeCount = useMemo(
    () => agents.filter((a) => isActive(a.last_seen_dt)).length,
    [agents],
  );
  const labeledCount = useMemo(
    () => agents.filter((a) => !!(a.display_name && a.display_name.trim())).length,
    [agents],
  );

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Sensible defaults: numeric counts desc, names asc.
      setSortDir(key === "label" ? "asc" : "desc");
    }
  };

  return (
    <>
      <PageHeader
        title="Agents"
        description="Every agent that has touched the gateway. Click any row for the per-agent detail page."
      />

      <div className="grid grid-cols-3 gap-3 mb-7">
        <MetricCard label="Total agents" value={agents.length} />
        <MetricCard
          label="Active (last 24h)"
          value={activeCount}
          tone={activeCount > 0 ? "success" : undefined}
        />
        <MetricCard
          label="With display name"
          value={`${labeledCount} / ${agents.length}`}
        />
      </div>

      <div className="mb-3 relative max-w-md">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by name or agent_id…"
          className="h-8 pl-7 text-xs"
        />
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : sorted.length === 0 ? (
        <p className="rounded-md border bg-card py-12 text-center text-sm text-muted-foreground">
          {agents.length === 0
            ? "No agents observed yet."
            : "No agents match the search."}
        </p>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHead label="Agent"      sortKey="label"      current={sortKey} dir={sortDir} onClick={toggleSort} />
                <SortableHead label="Trust"      sortKey="trust"     current={sortKey} dir={sortDir} onClick={toggleSort} alignRight />
                <SortableHead label="Sessions"   sortKey="sessions"   current={sortKey} dir={sortDir} onClick={toggleSort} alignRight />
                <SortableHead label="Entries"    sortKey="entries"    current={sortKey} dir={sortDir} onClick={toggleSort} alignRight />
                <SortableHead label="First seen" sortKey="first_seen" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <SortableHead label="Last seen"  sortKey="last_seen"  current={sortKey} dir={sortDir} onClick={toggleSort} />
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((a) => {
                const active = isActive(a.last_seen_dt);
                const labeled = !!(a.display_name && a.display_name.trim());
                return (
                  <TableRow key={a.agent_id} className="cursor-pointer hover:bg-accent/40">
                    <TableCell className="font-mono text-xs max-w-[280px] truncate" title={a.agent_id}>
                      <Link
                        to={`/agents/${encodeURIComponent(a.agent_id)}`}
                        className="text-primary hover:underline"
                      >
                        {labelOf(a)}
                      </Link>
                      {labeled && (
                        <span className="ml-2 text-[10px] text-muted-foreground" title={a.agent_id}>
                          {a.agent_id.slice(0, 18)}…
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {(() => {
                        const h = trustById.get(a.agent_id);
                        return h ? (
                          <span className="inline-flex justify-end">
                            <TrustBadge score={h.score} tierKey={h.tier_key} />
                          </span>
                        ) : (
                          <span className="font-mono text-xs text-muted-foreground">—</span>
                        );
                      })()}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {a.session_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {a.entry_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                      {a.first_seen_dt ? a.first_seen_dt.slice(0, 10) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                      {a.last_seen_dt ? a.last_seen_dt.slice(0, 16).replace("T", " ") : "—"}
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
      )}
    </>
  );
}

function SortableHead({
  label,
  sortKey,
  current,
  dir,
  onClick,
  alignRight,
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: "asc" | "desc";
  onClick: (k: SortKey) => void;
  alignRight?: boolean;
}) {
  const active = current === sortKey;
  return (
    <TableHead className={cn(alignRight && "text-right")}>
      <button
        type="button"
        onClick={() => onClick(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 text-[11px] uppercase tracking-wider font-semibold",
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        )}
      >
        {label}
        {active && <span className="text-[10px]">{dir === "asc" ? "▲" : "▼"}</span>}
      </button>
    </TableHead>
  );
}
