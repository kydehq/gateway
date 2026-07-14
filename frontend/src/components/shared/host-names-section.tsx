import { useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Save, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  useDeleteHostLabel,
  useHostLabels,
  useRefreshHostLabel,
  useUpsertHostLabel,
} from "@/api/queries";
import type { HostLabelRow, HostStatusFilter } from "@/api/types";
import { cn } from "@/lib/utils";

// Settings → "Host names" section. Single section, filter chips + search
// + unified table. Default chip "Unlabeled" (where actionable work is).

type Chip = HostStatusFilter;

const CHIPS: { key: Chip; label: string }[] = [
  { key: "all",              label: "All" },
  { key: "labeled",          label: "Labeled" },
  { key: "unlabeled",        label: "Unlabeled" },
  { key: "recently_active",  label: "Recently active" },
];

const SOURCE_STYLE: Record<string, string> = {
  admin:      "bg-primary/10 text-primary border-primary/20",
  dns:        "bg-muted text-muted-foreground border-border",
  "dns miss": "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
};

export function HostNamesSection({ readOnly = false }: { readOnly?: boolean }) {
  const [chip, setChip] = useState<Chip>("unlabeled");
  const [search, setSearch] = useState("");
  // Compose status param — chips are 1:1 with backend status values.
  const { data, isLoading } = useHostLabels(chip, search);
  const rows = data ?? [];

  // Counts come from re-querying the unfiltered "all" set so the chip
  // labels show how many rows each chip will surface. Avoids needing
  // separate counter endpoints.
  const { data: allRows } = useHostLabels("all", "");
  const counts = useMemo(() => {
    const c: Record<Chip, number> = {
      all: 0, labeled: 0, unlabeled: 0, recently_active: 0,
    };
    if (!allRows) return c;
    const now = Date.now() / 1000;
    for (const r of allRows) {
      c.all++;
      if (r.source === "admin") c.labeled++;
      else c.unlabeled++;
      if (r.last_seen && now - r.last_seen < 86400) c.recently_active++;
    }
    return c;
  }, [allRows]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {CHIPS.map((c) => {
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
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by IP or hostname…"
          className="h-8 max-w-xs text-xs ml-auto"
        />
      </div>

      <div className="rounded-md border">
        {isLoading ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {chip === "unlabeled" && counts.labeled > 0
              ? "Nothing left to label."
              : "No hosts match."}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>IP</TableHead>
                <TableHead>Hostname</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Last seen</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <HostNameRow key={row.ip} row={row} readOnly={readOnly} />
              ))}
            </TableBody>
          </Table>
        )}
        {rows.length >= 100 && (
          <div className="border-t bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
            Showing the first 100 — narrow the search to see more.
          </div>
        )}
      </div>
    </div>
  );
}

function HostNameRow({ row, readOnly }: { row: HostLabelRow; readOnly: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.hostname ?? "");

  const upsert = useUpsertHostLabel();
  const del = useDeleteHostLabel();
  const refresh = useRefreshHostLabel();

  const onSave = async () => {
    const value = draft.trim();
    if (!value) {
      toast.error("Hostname is required");
      return;
    }
    try {
      await upsert.mutateAsync({ ip: row.ip, hostname: value });
      toast.success(`Label set for ${row.ip}`);
      setEditing(false);
    } catch (err) {
      toast.error((err as Error).message || "Save failed");
    }
  };

  const onClear = async () => {
    try {
      await del.mutateAsync(row.ip);
      toast.success(`Cleared label for ${row.ip}`);
    } catch (err) {
      toast.error((err as Error).message || "Clear failed");
    }
  };

  const onRefresh = async () => {
    try {
      const result = await refresh.mutateAsync(row.ip);
      toast.success(
        result.hostname
          ? `Refreshed: ${row.ip} → ${result.hostname}`
          : `Refreshed: ${row.ip} (no PTR)`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Refresh failed");
    }
  };

  if (editing) {
    return (
      <TableRow>
        <TableCell className="font-mono text-xs">{row.ip}</TableCell>
        <TableCell colSpan={3}>
          <Input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="crm.internal"
            className="h-8 text-xs font-mono"
            onKeyDown={(e) => {
              if (e.key === "Enter") onSave();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        </TableCell>
        <TableCell className="text-right">
          <div className="flex justify-end gap-1">
            <Button size="sm" onClick={onSave} disabled={upsert.isPending}>
              <Save className="mr-1 h-3 w-3" /> Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </TableCell>
      </TableRow>
    );
  }

  const isLabeled = row.source === "admin";
  const sourceLabel = row.source ?? "—";

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{row.ip}</TableCell>
      <TableCell className="font-mono text-xs">
        {row.hostname ?? <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell>
        <span
          className={cn(
            "inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wide",
            SOURCE_STYLE[sourceLabel] ?? "bg-muted text-muted-foreground border-border",
          )}
        >
          {sourceLabel}
        </span>
      </TableCell>
      <TableCell className="font-mono text-[11px] text-muted-foreground">
        {row.last_seen_iso ? row.last_seen_iso.slice(0, 19).replace("T", " ") : "—"}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-1">
          {!isLabeled && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onRefresh}
              disabled={readOnly || refresh.isPending}
              title="Force a reverse-DNS refresh"
            >
              <RefreshCw className="mr-1 h-3 w-3" />
              Refresh
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={readOnly}
            onClick={() => {
              setDraft(row.hostname ?? "");
              setEditing(true);
            }}
          >
            {isLabeled ? "Edit" : row.source === "dns" ? "Override" : "Add label"}
          </Button>
          {isLabeled && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onClear}
              disabled={readOnly || del.isPending}
              title="Clear admin label (DNS will repopulate)"
            >
              <X className="mr-1 h-3 w-3" /> Clear
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}
