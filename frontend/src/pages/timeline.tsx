import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, Search, X } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { ActionBadge } from "@/components/shared/action-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { SortableTh, useSort } from "@/components/shared/sortable-th";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDebounced } from "@/hooks/use-debounced";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { useEntriesInfinite, useEntryFacets } from "@/api/queries";
import { useEntryRef } from "@/hooks/use-entry-ref";
import { truncate } from "@/lib/format";
import { cn } from "@/lib/utils";

const ALL = "__all__";

type SortKey = "seq" | "dt" | "agent_id" | "action_type" | "model" | "upstream" | "prompt_tokens" | "completion_tokens";

export default function TimelinePage() {
  const [params, setParams] = useSearchParams();
  const action = params.get("action") ?? "";
  const upstream = params.get("upstream") ?? "";
  const qParam = params.get("q") ?? "";

  const [qInput, setQInput] = useState(qParam);
  useEffect(() => setQInput(qParam), [qParam]);
  const qDebounced = useDebounced(qInput, 250);
  const searchRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcuts: `/` focuses search, `Esc` clears filters.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (e.key === "/" && !inField && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === "Escape" && inField && target === searchRef.current) {
        (target as HTMLInputElement).blur();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (qDebounced !== qParam) {
      const p = new URLSearchParams(params);
      if (qDebounced) p.set("q", qDebounced);
      else p.delete("q");
      setParams(p, { replace: true });
    }
  }, [qDebounced]);

  const setFilter = (key: "action" | "upstream" | "q", value: string) => {
    const p = new URLSearchParams(params);
    if (value && value !== ALL) p.set(key, value);
    else p.delete(key);
    setParams(p, { replace: true });
  };

  const clearAll = () => {
    const p = new URLSearchParams(params);
    ["action", "upstream", "q"].forEach((k) => p.delete(k));
    setParams(p, { replace: true });
    setQInput("");
  };

  const { data: facets } = useEntryFacets();

  const query = useEntriesInfinite({ action, upstream, q: qParam });
  const pages = query.data?.pages ?? [];
  const items = useMemo(() => pages.flatMap((p) => p.items), [pages]);

  const { sort, toggle } = useSort<SortKey>({ key: "seq", dir: "desc" });

  // Client-side sort on loaded rows. Server paginates by seq desc so the
  // default view already is the canonical ordering; sorting loaded rows
  // lets auditors pivot quickly without a round-trip.
  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (typeof av === "number" && typeof bv === "number") {
        return sort.dir === "asc" ? av - bv : bv - av;
      }
      const as = String(av ?? "").toLowerCase();
      const bs = String(bv ?? "").toLowerCase();
      return sort.dir === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
    });
    return copy;
  }, [items, sort]);

  const totalLoaded = items.length;

  const sentinelRef = useInfiniteScroll<HTMLDivElement>({
    onLoadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) query.fetchNextPage();
    },
    enabled: !!query.hasNextPage,
  });

  const { open } = useEntryRef();

  const exportCsv = () => {
    const header = [
      "seq", "dt", "agent_id", "action_type", "model", "upstream",
      "prompt_tokens", "completion_tokens", "session_id", "tool_count",
    ].join(",");
    const rows = items.map((e) =>
      [e.seq, e.dt, e.agent_id, e.action_type, e.model, e.upstream,
       e.prompt_tokens, e.completion_tokens, e.session_id, e.tool_count]
        .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
        .join(","),
    );
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "entries.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const activeFilters: Array<{ key: "action" | "upstream" | "q"; label: string; value: string }> = [];
  if (action)   activeFilters.push({ key: "action",   label: "action",   value: action });
  if (upstream) activeFilters.push({ key: "upstream", label: "upstream", value: upstream });
  if (qParam)   activeFilters.push({ key: "q",        label: "search",   value: qParam });

  return (
    <>
      <PageHeader
        title="Entry Timeline"
        description="Browse and filter all ledger entries"
        actions={
          <Button variant="outline" size="sm" onClick={exportCsv}>
            <Download className="mr-2 h-4 w-4" /> Export CSV
          </Button>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          {totalLoaded} loaded{query.hasNextPage ? "+" : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={searchRef}
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Search… ( / )"
              className="h-8 w-56 pl-7 text-xs"
            />
          </div>

          <Select value={action || ALL} onValueChange={(v) => setFilter("action", v)}>
            <SelectTrigger className="h-8 w-40 text-xs"><SelectValue placeholder="All actions" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All actions</SelectItem>
              {(facets?.action_types ?? []).map((a) => (
                <SelectItem key={a} value={a}>{a}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={upstream || ALL} onValueChange={(v) => setFilter("upstream", v)}>
            <SelectTrigger className="h-8 w-40 text-xs"><SelectValue placeholder="All upstreams" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All upstreams</SelectItem>
              {(facets?.upstreams ?? []).map((u) => (
                <SelectItem key={u} value={u}>{u}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {activeFilters.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {activeFilters.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key, "")}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs font-mono hover:bg-muted"
            >
              <span className="text-muted-foreground">{f.label}:</span>
              <span>{f.value}</span>
              <X className="h-3 w-3" />
            </button>
          ))}
          <button
            onClick={clearAll}
            className="text-xs text-muted-foreground underline hover:text-foreground"
          >
            Clear all
          </button>
        </div>
      ) : null}

      {/* Mobile card view (below sm). Shows the essentials without a
          horizontally-scrolling table. */}
      <div className="space-y-2 sm:hidden">
        {sorted.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No entries match the current filters.
          </p>
        ) : (
          sorted.map((e) => (
            <button
              key={e.seq}
              onClick={() => open(String(e.seq))}
              className="w-full rounded-md border border-border bg-card px-3 py-3 text-left hover:bg-accent/40"
            >
              <div className="mb-1 flex items-center gap-2">
                <ActionBadge type={e.action_type} />
                <span className="font-mono text-xs font-medium">#{e.seq}</span>
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  <RelativeTime value={e.dt} />
                </span>
              </div>
              <div className="font-mono text-xs text-foreground truncate">{e.agent_id}</div>
              <div className="font-mono text-[11px] text-muted-foreground truncate">{e.model}</div>
              <div className="mt-1 flex items-center justify-between font-mono text-[11px] text-muted-foreground">
                <span>{truncate(e.session_id, 16)}</span>
                <span>↑{e.prompt_tokens} ↓{e.completion_tokens}</span>
              </div>
            </button>
          ))
        )}
      </div>

      <div className="hidden overflow-x-auto rounded-md border border-border sm:block">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <SortableTh className="w-16" sortKey="seq"               sort={sort} toggle={toggle}>Seq</SortableTh>
              <SortableTh          sortKey="dt"                sort={sort} toggle={toggle}>Time</SortableTh>
              <SortableTh          sortKey="agent_id"          sort={sort} toggle={toggle}>Agent</SortableTh>
              <SortableTh          sortKey="action_type"       sort={sort} toggle={toggle}>Action</SortableTh>
              <SortableTh          sortKey="model"             sort={sort} toggle={toggle}>Model</SortableTh>
              <SortableTh          sortKey="upstream"          sort={sort} toggle={toggle}>Upstream</SortableTh>
              <SortableTh className="text-right" sortKey="prompt_tokens"     sort={sort} toggle={toggle}>↑ tokens</SortableTh>
              <SortableTh className="text-right" sortKey="completion_tokens" sort={sort} toggle={toggle}>↓ tokens</SortableTh>
              <TableHead>Session</TableHead>
              <TableHead>Tools</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.isLoading && items.length === 0 ? (
              Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={10}><Skeleton className="h-4 w-full" /></TableCell>
                </TableRow>
              ))
            ) : sorted.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="py-8 text-center text-sm text-muted-foreground">
                  No entries match the current filters.
                  {activeFilters.length > 0 ? (
                    <>
                      {" "}
                      <button className="underline hover:text-foreground" onClick={clearAll}>
                        Clear filters
                      </button>
                    </>
                  ) : null}
                </TableCell>
              </TableRow>
            ) : (
              sorted.map((e) => (
                <TableRow
                  key={e.seq}
                  className={cn("cursor-pointer")}
                  onClick={() => open(String(e.seq))}
                >
                  <TableCell className="font-mono text-xs">{e.seq}</TableCell>
                  <TableCell className="text-xs"><RelativeTime value={e.dt} /></TableCell>
                  <TableCell className="max-w-40 truncate font-mono text-xs">{e.agent_id}</TableCell>
                  <TableCell><ActionBadge type={e.action_type} /></TableCell>
                  <TableCell className="max-w-36 truncate font-mono text-xs">{e.model}</TableCell>
                  <TableCell className="text-xs">{e.upstream}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{e.prompt_tokens}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{e.completion_tokens}</TableCell>
                  <TableCell className="max-w-24 truncate font-mono text-xs">
                    {e.session_id ? (
                      <Link
                        to={`/sessions/${encodeURIComponent(e.session_id)}`}
                        className="text-primary hover:underline"
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        {truncate(e.session_id, 16)}
                      </Link>
                    ) : "-"}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-warning">
                    {e.tool_count ? `${e.tool_count}${e.first_tool ? ` · ${truncate(e.first_tool, 16)}` : ""}` : ""}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div ref={sentinelRef} className="h-4" />
      {query.isFetchingNextPage ? (
        <div className="py-4 text-center text-xs text-muted-foreground">Loading more…</div>
      ) : null}
    </>
  );
}
