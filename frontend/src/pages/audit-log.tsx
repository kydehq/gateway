import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Search, X } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { ActionBadge } from "@/components/shared/action-badge";
import { RelativeTime } from "@/components/shared/relative-time";
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDebounced } from "@/hooks/use-debounced";
import { useFeatures } from "@/hooks/use-features";
import { LockedMetric } from "@/components/shared/upgrade-lock";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { downloadFile, downloadPdf } from "@/api/client";
import {
  STATS_WINDOWS,
  useDlpAlerts,
  useEntriesInfinite,
  useEntryFacets,
  useSessionsInfinite,
  useVerify,
  type StatsWindow,
} from "@/api/queries";
import { NavLink, useSearchParams } from "react-router-dom";
import { useEntryRef } from "@/hooks/use-entry-ref";
import { formatAlertId, formatSeqId, formatSessionId } from "@/lib/serial-ids";
import { useAgentLabel } from "@/hooks/use-agent-label";
import { fmtTokens } from "@/lib/format";

const ALL = "__all__";

function rewriteProvider(raw: string): string {
  if (!raw || raw === "(none)") return "Direct (no provider)";
  if (raw.includes("anthropic")) return "Anthropic";
  if (raw.includes("openai")) return "OpenAI";
  if (raw.includes("google")) return "Google";
  if (raw.includes("azure")) return "Azure OpenAI";
  if (raw.includes("bedrock")) return "AWS Bedrock";
  return raw;
}

const WINDOW_LABEL: Record<StatsWindow, string> = {
  "1h": "Last 1h",
  "24h": "Last 24h",
  "7d": "Last 7d",
  "30d": "Last 30d",
  "90d": "Last 90d",
  all: "All time",
};

export default function AuditLogPage() {
  // URL query params let other pages route here pre-filtered to a
  // particular session or agent. The Clear button strips them along
  // with the other filters.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSession = searchParams.get("session") ?? "";
  const urlAgent = searchParams.get("agent") ?? "";

  const [action, setAction] = useState(ALL);
  const [upstream, setUpstream] = useState(ALL);
  const [window, setWindow] = useState<StatsWindow>(urlSession || urlAgent ? "all" : "24h");
  const [qInput, setQInput] = useState("");
  const qDebounced = useDebounced(qInput, 250);
  const searchRef = useRef<HTMLInputElement>(null);

  const { data: facets } = useEntryFacets();
  const { data: verify } = useVerify();
  const { signingEnabled } = useFeatures();
  const { shortLabel: agentShort } = useAgentLabel();
  const { data: alerts = [] } = useDlpAlerts();
  // Sessions lookup powers the Session column's SES-#### links — match the
  // audit-log's current window so we don't show "SES-?" for sessions the
  // user can already see in the table.
  const sessionsQuery = useSessionsInfinite({
    window,
    has_alert: "any",
    agents: [],
    sort: "newest",
    status: [],
  });
  // Build (entry_id → alert_serial[]) and (session_id → serial_id) maps once
  // per data refresh. Entries whose session isn't in the cached first page
  // fall back to "—" — that's acceptable for an audit log where most
  // user-driven exploration is recent.
  const alertsByEntry = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const a of alerts) {
      const eid = a.entry_id;
      if (!eid) continue;
      const serial = (a.serial_id ?? a.id) as number;
      const prev = m.get(eid) ?? [];
      prev.push(typeof serial === "number" ? serial : Number(serial));
      m.set(eid, prev);
    }
    return m;
  }, [alerts]);
  const sessionSerialById = useMemo(() => {
    const m = new Map<string, number>();
    for (const page of sessionsQuery.data?.pages ?? []) {
      for (const s of page.items) {
        if (s.serial_id != null) m.set(s.session_id, s.serial_id);
      }
    }
    return m;
  }, [sessionsQuery.data]);

  const queryParams = useMemo(
    () => ({
      action: action !== ALL ? action : undefined,
      upstream: upstream !== ALL ? upstream : undefined,
      agent_id: urlAgent || undefined,
      session_id: urlSession || undefined,
      q: qDebounced || undefined,
      window,
    }),
    [action, upstream, qDebounced, window, urlAgent, urlSession],
  );

  const query = useEntriesInfinite(queryParams);
  const pages = query.data?.pages ?? [];
  const items = useMemo(() => pages.flatMap((p) => p.items), [pages]);
  // `total_count` reflects the filtered set, not the entire ledger — useful
  // for "Showing N of M" labels in the filter bar.
  const totalCount = pages[0]?.total_count;
  const { open } = useEntryRef();

  const sentinelRef = useInfiniteScroll<HTMLDivElement>({
    onLoadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) query.fetchNextPage();
    },
    enabled: !!query.hasNextPage,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        !!target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (e.key === "/" && !inField && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // "hasFilters" is what drives the Clear button — only fires for filters
  // the user *added*. Window has its own default ("24h"), so we only count
  // it as an active filter when non-default.
  const hasFilters =
    action !== ALL ||
    upstream !== ALL ||
    !!qDebounced ||
    window !== "24h" ||
    !!urlSession ||
    !!urlAgent;

  const clearAll = () => {
    setAction(ALL);
    setUpstream(ALL);
    setQInput("");
    setWindow("24h");
    // Also strip ?session= / ?agent= from the URL.
    setSearchParams(new URLSearchParams());
  };

  const dateRange = useMemo(() => {
    if (!items.length) return "—";
    const sorted = [...items].sort((a, b) => a.dt.localeCompare(b.dt));
    const first = new Date(sorted[0].dt).toLocaleDateString("de-DE");
    const last = new Date(sorted[sorted.length - 1].dt).toLocaleDateString("de-DE");
    return first === last ? first : `${first} – ${last}`;
  }, [items]);

  return (
    <>
      <PageHeader
        title="Audit Log"
        description="Immutable ledger of all agent actions — every entry signed and chained."
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                try {
                  await downloadFile(
                    "/api/export/audit-log-csv",
                    {
                      action: queryParams.action,
                      upstream: queryParams.upstream,
                      q: queryParams.q,
                      window: queryParams.window,
                      limit: 5000,
                    },
                    "audit-log.csv",
                    "text/csv",
                  );
                  toast.success("Audit log CSV downloaded");
                } catch (err) {
                  toast.error((err as Error).message || "Export failed");
                }
              }}
            >
              <Download className="h-3.5 w-3.5 mr-1.5" /> Export CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                try {
                  await downloadPdf(
                    "/api/export/audit-log",
                    {
                      action: queryParams.action,
                      upstream: queryParams.upstream,
                      q: queryParams.q,
                      window: queryParams.window,
                      limit: 500,
                    },
                    "audit-log.pdf",
                  );
                  toast.success("Audit log downloaded");
                } catch (err) {
                  toast.error((err as Error).message || "Export failed");
                }
              }}
            >
              🛡 Export PDF
            </Button>
          </div>
        }
      />

      {/* KPI block */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        <MetricCard
          label="Total Entries"
          value={
            totalCount !== undefined
              ? totalCount.toLocaleString()
              : items.length + (query.hasNextPage ? "+" : "")
          }
          subtext={
            totalCount !== undefined && items.length < totalCount
              ? `Showing ${items.length.toLocaleString()} of ${totalCount.toLocaleString()}`
              : undefined
          }
        />
        {signingEnabled ? (
          <>
            <MetricCard
              label="Chain Integrity"
              value={verify ? (verify.valid ? "VERIFIED" : "BROKEN") : "—"}
              tone={verify ? (verify.valid ? "success" : "destructive") : undefined}
            />
            <MetricCard
              label="Signature Failures"
              value={verify?.signature_failures ?? "—"}
              tone={(verify?.signature_failures ?? 0) > 0 ? "destructive" : undefined}
            />
          </>
        ) : (
          // Sandbox is observe-only: the verifiable audit ledger (integrity
          // verification + signatures) is Enterprise-only. The entries list
          // below stays fully available.
          <>
            <LockedMetric label="Chain Integrity" />
            <LockedMetric label="Signature Failures" />
          </>
        )}
        <MetricCard label="Date Range" value={dateRange} small />
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-48">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search entries… (/)"
            className="h-8 pl-7 text-xs"
          />
        </div>
        <Select value={window} onValueChange={(v) => setWindow(v as StatsWindow)}>
          <SelectTrigger className="h-8 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATS_WINDOWS.map((w) => (
              <SelectItem key={w} value={w}>{WINDOW_LABEL[w]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={action} onValueChange={setAction}>
          <SelectTrigger className="h-8 w-40 text-xs">
            <SelectValue placeholder="Action type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All actions</SelectItem>
            {(facets?.action_types ?? []).map((a) => (
              <SelectItem key={a} value={a}>{a}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={upstream} onValueChange={setUpstream}>
          <SelectTrigger className="h-8 w-44 text-xs">
            <SelectValue placeholder="AI Provider" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All providers</SelectItem>
            {(facets?.upstreams ?? []).map((u) => (
              <SelectItem key={u} value={u}>{rewriteProvider(u)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {hasFilters && (
          <Button variant="ghost" size="sm" className="h-8 gap-1 text-xs text-muted-foreground" onClick={clearAll}>
            <X className="h-3 w-3" /> Clear
          </Button>
        )}
      </div>

      {/* Inbound-link banner: shown when another page routed us here with
          ?session= or ?agent=. Makes the filter visible and clearable. */}
      {(urlSession || urlAgent) && (
        <div className="mb-3 flex items-center justify-between rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
          <span className="font-mono text-xs">
            {urlSession && <>Filtered to session <strong>{urlSession}</strong></>}
            {urlAgent && <>Filtered to agent <strong>{urlAgent}</strong></>}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={() => setSearchParams(new URLSearchParams())}
          >
            <X className="h-3 w-3" /> Clear
          </Button>
        </div>
      )}

      {/* Entries table */}
      {query.isLoading && items.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : items.length === 0 ? (
        <p className="py-16 text-center text-sm text-muted-foreground">No entries match.</p>
      ) : (
        <TooltipProvider delayDuration={400}>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="font-mono text-xs w-24">Seq</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Session</TableHead>
                <TableHead>AI Provider</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Prompt</TableHead>
                <TableHead className="text-right">Response</TableHead>
                <TableHead className="w-20">Alert</TableHead>
                <TableHead>Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => {
                const rowAlerts = row.entry_id ? alertsByEntry.get(row.entry_id) ?? [] : [];
                const sessionSerial = row.session_id ? sessionSerialById.get(row.session_id) : undefined;
                // Hover preview wraps the row when an auditor-gated
                // why_preview is available. ~400ms feels intentional
                // without being slow.
                const rowEl = (
                  <TableRow
                    key={row.seq}
                    className="cursor-pointer hover:bg-accent/40"
                    onClick={() => open(String(row.seq))}
                  >
                    <TableCell className="font-mono text-xs text-muted-foreground">{formatSeqId(row.seq)}</TableCell>
                    <TableCell><ActionBadge type={row.action_type} /></TableCell>
                    <TableCell className="text-sm text-muted-foreground max-w-[120px] truncate">
                      {row.agent_id ? (
                        <NavLink
                          to={`/agents/${encodeURIComponent(row.agent_id)}`}
                          className="hover:text-foreground hover:underline"
                          onClick={(e) => e.stopPropagation()}
                          title={`Open agent ${row.agent_id}`}
                        >
                          {agentShort(row.agent_id)}
                        </NavLink>
                      ) : (
                        agentShort(row.agent_id)
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {row.session_id ? (
                        <NavLink
                          to={`/sessions/${row.session_id}`}
                          className="text-muted-foreground hover:text-foreground hover:underline"
                          onClick={(e) => e.stopPropagation()}
                          title={row.session_id}
                        >
                          {sessionSerial !== undefined ? formatSessionId(sessionSerial) : "SES-?"}
                        </NavLink>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {rewriteProvider(row.upstream)}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground max-w-[100px] truncate">
                      {row.model || "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {row.prompt_tokens ? fmtTokens(row.prompt_tokens) : "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {row.completion_tokens ? fmtTokens(row.completion_tokens) : "—"}
                    </TableCell>
                    <TableCell>
                      {rowAlerts.length > 0 ? (
                        <span
                          className="rounded bg-destructive/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-destructive"
                          title={rowAlerts.map((s) => formatAlertId(s)).join(", ")}
                        >
                          ⚠ {rowAlerts.length}
                        </span>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      <RelativeTime value={row.dt} />
                    </TableCell>
                  </TableRow>
                );
                if (!row.why_preview) return rowEl;
                return (
                  <Tooltip key={row.seq}>
                    <TooltipTrigger asChild>{rowEl}</TooltipTrigger>
                    <TooltipContent
                      side="bottom"
                      align="start"
                      className="max-w-[480px] whitespace-pre-wrap font-mono text-[11px]"
                    >
                      {row.why_preview}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </TableBody>
          </Table>
        </div>
        </TooltipProvider>
      )}

      <div ref={sentinelRef} className="h-4" />
      {query.isFetchingNextPage && (
        <div className="py-2 text-center text-xs text-muted-foreground">Loading more…</div>
      )}
    </>
  );
}
