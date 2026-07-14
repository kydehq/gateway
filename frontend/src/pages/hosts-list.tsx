import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useHostLabels } from "@/api/queries";
import type { HostLabelRow } from "@/api/types";
import { cn } from "@/lib/utils";

// Browse-all-hosts list. Reuses /api/host-labels?status=all — same feed
// the Settings → Host Names section uses, but with a different UI shape:
// browse-oriented (no inline edit, no labeling actions) and reachable
// from auditor-level nav. Click any row → /hosts/{ip}.

type SortKey = "host" | "source" | "last_seen";
type SourceChip = "all" | "labeled" | "dns" | "miss" | "none";

const SOURCE_STYLE: Record<string, string> = {
  admin:      "bg-primary/10 text-primary border-primary/20",
  dns:        "bg-muted text-muted-foreground border-border",
  "dns miss": "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
};

function matchesChip(row: HostLabelRow, chip: SourceChip): boolean {
  if (chip === "all")     return true;
  if (chip === "labeled") return row.source === "admin";
  if (chip === "dns")     return row.source === "dns";
  if (chip === "miss")    return row.source === "dns miss";
  if (chip === "none")    return row.source === null;
  return true;
}

export default function HostsListPage() {
  // Fetch full set once; the Settings table tops out at 100 per call,
  // which is fine for browsing. If a deployment ever exceeds 100 hosts
  // we'll add the same search-server-side pattern the Settings table
  // uses.
  const { data: rows = [], isLoading } = useHostLabels("all", "");
  const [search, setSearch] = useState("");
  const [chip, setChip] = useState<SourceChip>("all");
  const [sortKey, setSortKey] = useState<SortKey>("last_seen");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows
      .filter((r) => matchesChip(r, chip))
      .filter((r) => {
        if (!q) return true;
        return (
          r.ip.toLowerCase().includes(q) ||
          (r.hostname?.toLowerCase().includes(q) ?? false)
        );
      });
  }, [rows, chip, search]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: number | string;
      let bv: number | string;
      switch (sortKey) {
        case "host":
          // Sort by hostname when present, else by IP — so labeled hosts
          // cluster near each other by name.
          av = (a.hostname ?? a.ip).toLowerCase();
          bv = (b.hostname ?? b.ip).toLowerCase();
          break;
        case "source":
          av = a.source ?? "";
          bv = b.source ?? "";
          break;
        case "last_seen":
        default:
          // NULL last_seen sorts last regardless of direction.
          av = a.last_seen ?? -Infinity;
          bv = b.last_seen ?? -Infinity;
          break;
      }
      if (av === bv) return 0;
      const cmp = av < bv ? -1 : 1;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  // Counts for the chips. Computed off the unfiltered rows so the chip
  // labels stay stable as the user types in search.
  const counts = useMemo(() => {
    const c: Record<SourceChip, number> = {
      all: rows.length,
      labeled: 0,
      dns: 0,
      miss: 0,
      none: 0,
    };
    for (const r of rows) {
      if (r.source === "admin") c.labeled++;
      else if (r.source === "dns") c.dns++;
      else if (r.source === "dns miss") c.miss++;
      else if (r.source === null) c.none++;
    }
    return c;
  }, [rows]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "host" ? "asc" : "desc");
    }
  };

  const chips: { key: SourceChip; label: string }[] = [
    { key: "all",     label: "All" },
    { key: "labeled", label: "Labeled" },
    { key: "dns",     label: "DNS" },
    { key: "miss",    label: "DNS miss" },
    { key: "none",    label: "Unresolved" },
  ];

  return (
    <>
      <PageHeader
        title="Hosts"
        description="Every IP that has reached the gateway, plus admin-labeled hosts. Click any row to open its detail page."
      />

      <div className="grid grid-cols-3 gap-3 mb-7">
        <MetricCard label="Total hosts" value={rows.length} />
        <MetricCard
          label="Labeled"
          value={counts.labeled}
          subtext={
            rows.length > 0
              ? `${Math.round((counts.labeled / rows.length) * 100)}% named`
              : undefined
          }
        />
        <MetricCard
          label="DNS misses"
          value={counts.miss}
          tone={counts.miss > 0 ? "warning" : undefined}
          subtext={
            counts.miss > 0
              ? "IPs with no PTR — label in Settings"
              : undefined
          }
        />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {chips.map((c) => {
          const active = chip === c.key;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => setChip(c.key)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors",
                active
                  ? "border-foreground bg-foreground/10"
                  : "border-border bg-card hover:border-foreground/40",
              )}
            >
              {c.label}
              <span className="ml-1.5 text-muted-foreground">{counts[c.key]}</span>
            </button>
          );
        })}
        <div className="relative ml-auto max-w-xs flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by IP or hostname…"
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : sorted.length === 0 ? (
        <p className="rounded-md border bg-card py-12 text-center text-sm text-muted-foreground">
          {rows.length === 0
            ? "No hosts observed yet."
            : "No hosts match the current filter."}
        </p>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHead label="Host"      sortKey="host"      current={sortKey} dir={sortDir} onClick={toggleSort} />
                <TableHead>IP</TableHead>
                <SortableHead label="Source"    sortKey="source"    current={sortKey} dir={sortDir} onClick={toggleSort} />
                <SortableHead label="Last seen" sortKey="last_seen" current={sortKey} dir={sortDir} onClick={toggleSort} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row) => (
                <TableRow key={row.ip} className="cursor-pointer hover:bg-accent/40">
                  <TableCell className="font-mono text-xs">
                    <Link
                      to={`/hosts/${encodeURIComponent(row.ip)}`}
                      className="text-primary hover:underline"
                    >
                      {row.hostname ?? <span className="text-muted-foreground">(unresolved)</span>}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">
                    {row.ip}
                  </TableCell>
                  <TableCell>
                    <span
                      className={cn(
                        "inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wide",
                        SOURCE_STYLE[row.source ?? ""] ?? "bg-muted text-muted-foreground border-border",
                      )}
                    >
                      {row.source ?? "—"}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                    {row.last_seen_iso ? row.last_seen_iso.slice(0, 19).replace("T", " ") : "—"}
                  </TableCell>
                </TableRow>
              ))}
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
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: "asc" | "desc";
  onClick: (k: SortKey) => void;
}) {
  const active = current === sortKey;
  return (
    <TableHead>
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
