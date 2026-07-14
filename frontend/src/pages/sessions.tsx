import { useEffect, useMemo, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/page-header";
import { ActionBadge } from "@/components/shared/action-badge";
import { MetricCard } from "@/components/shared/metric-card";
import { RelativeTime } from "@/components/shared/relative-time";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { useEntryRef } from "@/hooks/use-entry-ref";
import {
  SESSION_SORTS,
  STATS_WINDOWS,
  useDlpAlert,
  useDlpAlerts,
  useSession,
  useSessionsInfinite,
  type HasAlertFilter,
  type SessionSort,
  type StatsWindow,
} from "@/api/queries";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { DlpAlertDetail } from "@/components/shared/dlp-alert-detail";
import { useMe } from "@/hooks/use-me";
import { downloadPdf } from "@/api/client";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { truncate } from "@/lib/format";
import { formatAlertId, formatSessionId } from "@/lib/serial-ids";
import { getSessionDisplayName } from "@/lib/session-names";
import { cn } from "@/lib/utils";
import {
  KIND_TONE_CLASSES,
  describeKind,
  hasChatBody,
  synthesise,
} from "@/lib/request-kind";
import type { RequestKind, SessionSummary } from "@/api/types";

const LONG_MESSAGE_THRESHOLD = 200;

// Strip the backend's `[role] ...` prefix from why_last — we render the role
// as a separate chip below. Backend format is `f"[{role}] {content}"`.
function splitRoleAndBody(whyLast: string | undefined | null):
  { role: string | null; body: string } {
  if (!whyLast) return { role: null, body: "" };
  const m = whyLast.match(/^\[([a-zA-Z_]+)\]\s*(.*)$/s);
  if (!m) return { role: null, body: whyLast };
  return { role: m[1].toLowerCase(), body: m[2] };
}

const ROLE_TAG_STYLE: Record<string, string> = {
  user: "bg-primary/15 text-primary",
  assistant: "bg-chart-4/15 text-chart-4",
  agent: "bg-chart-4/15 text-chart-4",
  system: "bg-muted text-muted-foreground",
  tool: "bg-warning/15 text-warning",
};

function ContentTag({ label, tone = "muted" }: { label: string; tone?: "muted" | "user" | "agent" | "tool" }) {
  const className =
    tone === "user" ? ROLE_TAG_STYLE.user :
    tone === "agent" ? ROLE_TAG_STYLE.assistant :
    tone === "tool" ? ROLE_TAG_STYLE.tool :
    ROLE_TAG_STYLE.system;
  return (
    <span className={cn("rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider", className)}>
      {label}
    </span>
  );
}

// Inline entry body: classification tags + collapsed long messages.
// Click-to-expand stops propagation so the parent's entry-detail open
// doesn't fire — long messages need a read-in-place affordance separate
// from the "open entry" interaction.
//
// When the entry has no chat body (tool-only, streaming-partial, empty
// request/content, policy_block, unknown), render a kind-driven synthesis
// line instead of empty space — the request_kind chip tells the operator
// *why* the row is empty, and the synthesis surfaces the few useful
// signals we still have (model, tokens, first tool name).
function KindChip({ kind }: { kind: RequestKind | undefined }) {
  const desc = describeKind(kind);
  return (
    <span
      className={cn(
        "rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
        KIND_TONE_CLASSES[desc.tone],
      )}
    >
      {desc.chip}
    </span>
  );
}

function EntryBody({
  whyLast,
  hasToolCall,
  kind,
  model,
  upstream,
  promptTokens,
  completionTokens,
  toolCount,
  firstTool,
}: {
  whyLast: string | undefined;
  hasToolCall: boolean;
  kind?: RequestKind;
  model?: string;
  upstream?: string;
  promptTokens?: number;
  completionTokens?: number;
  toolCount?: number;
  firstTool?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const { role, body } = splitRoleAndBody(whyLast);
  const isLong = body.length > LONG_MESSAGE_THRESHOLD;
  const showSynthesis = !body && !hasChatBody(kind);

  if (!body && !hasToolCall && !showSynthesis) return null;

  const roleLabel =
    role === "user" ? "USER" :
    role === "assistant" || role === "agent" ? "AGENT" :
    role === "system" ? "SYSTEM" :
    role === "tool" ? "TOOL" :
    null;
  const roleTone: "user" | "agent" | "tool" | "muted" =
    role === "user" ? "user" :
    role === "assistant" || role === "agent" ? "agent" :
    role === "tool" ? "tool" :
    "muted";

  return (
    <div className="text-xs text-muted-foreground leading-relaxed">
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        {roleLabel ? <ContentTag label={roleLabel} tone={roleTone} /> : null}
        {hasToolCall ? <ContentTag label="TOOL CALL" tone="tool" /> : null}
        {/* Always show the kind chip when it's something other than plain
            "chat" — it communicates the row's nature even when body is
            present (e.g., a chat row that *also* has a policy block). */}
        {kind && kind !== "chat" ? <KindChip kind={kind} /> : null}
      </div>
      {body ? (
        <>
          <span>{expanded || !isLong ? body : truncate(body, LONG_MESSAGE_THRESHOLD)}</span>
          {isLong ? (
            <button
              type="button"
              className="ml-2 font-mono text-[11px] text-primary hover:underline"
              onClick={(ev) => {
                ev.stopPropagation();
                setExpanded((s) => !s);
              }}
            >
              {expanded ? "show less" : "show more"}
            </button>
          ) : null}
        </>
      ) : showSynthesis ? (
        <span className="italic">
          {synthesise(kind, {
            model,
            upstream,
            promptTokens,
            completionTokens,
            toolCount,
            firstTool,
          })}
        </span>
      ) : null}
    </div>
  );
}

function SessionDetailPanel({ sessionId }: { sessionId: string }) {
  const { data, isLoading, isError, error } = useSession(sessionId);
  const { open } = useEntryRef();
  const navigate = useNavigate();
  const { isAuditor } = useMe();
  // The session API returns a slim per-entry alert summary, not the full
  // DlpAlert — fetch the selected one on demand so the right-side sheet
  // can render the same detail view used on /threats-alerts.
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const { data: selectedAlert } = useDlpAlert(selectedAlertId);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-destructive">
        Failed to load session: {(error as Error)?.message}
      </p>
    );
  }
  if (!data) return null;

  const agents = new Set(data.entries.map((e) => e.agent_id)).size;
  const chronological = [...data.entries].sort((a, b) => a.seq - b.seq);
  const first = chronological[0];
  const last = chronological[chronological.length - 1];
  const entries = [...chronological].reverse();

  return (
    <div>
      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 mb-5">
        <MetricCard label="Entries" value={entries.length} />
        <MetricCard label="Agents" value={agents} />
        <MetricCard label="Start" small value={first?.dt ?? "-"} />
        <MetricCard label="End" small value={last?.dt ?? "-"} />
      </div>

      {/* Agents involved — each chip routes to /agents/<id>. Distinct
          agent_ids drawn from the entries, so multi-agent sessions
          surface all participants. */}
      {(() => {
        const distinctAgents = Array.from(
          new Set(data.entries.map((e) => e.agent_id).filter(Boolean)),
        );
        if (distinctAgents.length === 0) return null;
        return (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
              Agents:
            </span>
            {distinctAgents.map((aid) => (
              <NavLink
                key={aid}
                to={`/agents/${encodeURIComponent(aid)}`}
                className="rounded border border-border bg-card px-2 py-0.5 font-mono text-[11px] hover:border-primary/40 hover:text-primary"
                title={`Open agent ${aid}`}
              >
                {aid}
              </NavLink>
            ))}
          </div>
        );
      })()}

      {/* Hosts involved — distinct client IPs across the session, each
          linked to /hosts/<ip>. Backend annotates with the cached
          hostname so we render "hostname (ip)" without a second
          round-trip. Plural by design: NAT churn or mobile roaming can
          spread one session across IPs. */}
      {data.hosts && data.hosts.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
            {data.hosts.length === 1 ? "Host:" : "Hosts:"}
          </span>
          {data.hosts.map((h) => (
            <NavLink
              key={h.ip}
              to={`/hosts/${encodeURIComponent(h.ip)}`}
              className="rounded border border-border bg-card px-2 py-0.5 text-[11px] hover:border-primary/40 hover:text-primary"
              title={`Open host ${h.hostname ?? h.ip}`}
            >
              {h.hostname ? (
                <>
                  {h.hostname}{" "}
                  <span className="font-mono text-muted-foreground">({h.ip})</span>
                </>
              ) : (
                <span className="font-mono">{h.ip}</span>
              )}
            </NavLink>
          ))}
        </div>
      )}

      {/* Session ID + action toolbar */}
      <div className="flex items-center justify-between mb-4 gap-2 flex-wrap">
        <div className="font-mono text-[11px] text-muted-foreground">
          SESSION: {sessionId}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                await downloadPdf(
                  "/api/export/compliance-evidence",
                  { kind: "session", id: sessionId },
                  `session-${sessionId}.pdf`,
                );
                toast.success("Evidence downloaded");
              } catch (err) {
                toast.error((err as Error).message || "Export failed");
              }
            }}
          >
            🛡 Export Evidence
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => navigate(`/audit-log?session=${encodeURIComponent(sessionId)}`)}
          >
            Full audit trail →
          </Button>
        </div>
      </div>

      {/* Entry timeline */}
      <div className="ml-6 border-l-2 border-border pl-6">
        {entries.map((e) => (
          <div
            key={e.seq}
            className="relative cursor-pointer border-b border-border py-3 pl-5 last:border-0 hover:bg-accent/30"
            onClick={() => open(String(e.seq))}
          >
            <span
              className={cn(
                "absolute -left-[29px] top-4 block h-2.5 w-2.5 rounded-full border-2 bg-background",
                e.action_type === "chat"          ? "border-primary" :
                e.action_type === "tool_call"     ? "border-warning" :
                e.action_type === "tool_result"   ? "border-success" :
                e.action_type === "error"         ? "border-destructive" :
                e.action_type === "policy_block"  ? "border-destructive" :
                e.action_type === "auth"          ? "border-chart-4" :
                "border-border",
              )}
            />
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-muted-foreground">
                <RelativeTime value={e.dt} />
              </span>
              <ActionBadge type={e.action_type} />
              <span className="font-mono text-[11px] text-muted-foreground/70">{e.model}</span>
            </div>
            <EntryBody
              whyLast={e.why_last}
              hasToolCall={(e.tool_count ?? 0) > 0}
              kind={e.request_kind}
              model={e.model}
              upstream={e.upstream}
              promptTokens={e.prompt_tokens}
              completionTokens={e.completion_tokens}
              toolCount={e.tool_count}
              firstTool={(e.tool_calls ?? [])[0]?.function ?? null}
            />
            {e.tool_count ? (
              <div className="mt-1 font-mono text-xs text-warning">
                {(e.tool_calls ?? []).map((tc) => tc.function || "?").join(", ")}
              </div>
            ) : null}
            {e.dlp_alerts && e.dlp_alerts.length > 0 && (
              <div
                className="mt-2 flex flex-wrap items-center gap-2 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs"
                onClick={(ev) => ev.stopPropagation()}
              >
                <span className="font-mono font-semibold text-destructive">
                  ⚠ DLP alert{e.dlp_alerts.length > 1 ? "s" : ""}
                </span>
                {e.dlp_alerts.map((a) => (
                  <button
                    key={a.alert_id}
                    type="button"
                    className="rounded border border-transparent px-1.5 py-0.5 font-mono text-destructive/80 hover:border-destructive/40 hover:bg-destructive/10"
                    onClick={() => setSelectedAlertId(a.alert_id)}
                    title="Open alert details"
                  >
                    {formatAlertId(a.serial_id)}
                    {a.severity ? ` · ${a.severity.toUpperCase()}` : ""}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Audit footer */}
      <div className="mt-6 rounded-md border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">Audit confirmation:</span>{" "}
        All entries in this session are recorded in the immutable ledger and cryptographically chained.
        Chain integrity is verified on every API response.
      </div>

      <Sheet
        open={!!selectedAlertId}
        onOpenChange={(o) => !o && setSelectedAlertId(null)}
      >
        <SheetContent className="w-[480px] sm:max-w-[480px] overflow-y-auto">
          {selectedAlert && (
            <DlpAlertDetail
              alert={selectedAlert}
              isAuditor={isAuditor}
              onEntityLinkClick={() => setSelectedAlertId(null)}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function SessionListItem({
  s,
  isActive,
  alertCount,
}: {
  s: SessionSummary;
  isActive: boolean;
  alertCount: number;
}) {
  const displayName = useMemo(() => getSessionDisplayName(s), [s]);
  const sid = formatSessionId(s.serial_id);

  return (
    <NavLink
      to={`/sessions/${s.session_id}`}
      className={cn(
        "mb-3 block rounded-md border p-4 transition-colors",
        isActive ? "border-primary bg-card" : "border-border bg-card hover:border-muted-foreground/40",
        alertCount > 0 ? "border-destructive/40" : undefined,
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-semibold text-foreground">{sid}</span>
        <span className="font-mono text-[11px] text-muted-foreground shrink-0">
          {alertCount > 0 ? (
            <span className="mr-2 rounded bg-destructive/15 px-1.5 py-0.5 font-semibold text-destructive">
              ⚠ {alertCount}
            </span>
          ) : null}
          {s.entry_count} entries
        </span>
      </div>
      <div className="text-xs text-muted-foreground mb-1 truncate">{displayName}</div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span><strong className="text-foreground">{s.agent_count}</strong> agent(s)</span>
        <span><RelativeTime value={s.last_time} /></span>
      </div>
    </NavLink>
  );
}

const WINDOW_LABEL: Record<StatsWindow, string> = {
  "1h": "Last 1h",
  "24h": "Last 24h",
  "7d": "Last 7d",
  "30d": "Last 30d",
  "90d": "Last 90d",
  all: "All time",
};
const SORT_LABEL: Record<SessionSort, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  entries: "Most entries",
  agents: "Most agents",
};
const HAS_ALERT_LABEL: Record<HasAlertFilter, string> = {
  any: "Any",
  yes: "With alerts",
  no: "No alerts",
};

export default function SessionsPage() {
  const [window, setWindow] = useState<StatsWindow>("24h");
  const [hasAlert, setHasAlert] = useState<HasAlertFilter>("any");
  const [agentFilter, setAgentFilter] = useState<string>("__all__");
  const [sort, setSort] = useState<SessionSort>("newest");

  const query = useSessionsInfinite({
    window,
    has_alert: hasAlert,
    agents: agentFilter === "__all__" ? [] : [agentFilter],
    sort,
    status: [],
  });
  const pages = query.data?.pages ?? [];
  const items = useMemo(() => pages.flatMap((p) => p.items), [pages]);
  const { data: alerts = [] } = useDlpAlerts();
  // Build session_id → open-alert-count map once per alerts refresh.
  // "Open" = anything not closed; closed alerts shouldn't visually nag.
  const alertCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of alerts) {
      if (a.status === "closed") continue;
      const sid = a.session_id ?? "";
      if (!sid) continue;
      m.set(sid, (m.get(sid) ?? 0) + 1);
    }
    return m;
  }, [alerts]);
  const { sessionId } = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (s) =>
        s.session_id.toLowerCase().includes(q) ||
        getSessionDisplayName(s).toLowerCase().includes(q),
    );
  }, [items, search]);

  // Agent options come from the currently-loaded sessions. A "true" all-time
  // roster would need a separate fetch — acceptable trade-off since the
  // window already scopes the user's intent.
  const agentOptions = useMemo(() => {
    const set = new Set<string>();
    for (const s of items) {
      for (const a of s.agents ?? []) if (a) set.add(a);
    }
    return Array.from(set).sort();
  }, [items]);

  useEffect(() => {
    if (!sessionId && items[0]) navigate(`/sessions/${items[0].session_id}`, { replace: true });
  }, [sessionId, items, navigate]);

  const sentinelRef = useInfiniteScroll<HTMLDivElement>({
    onLoadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) query.fetchNextPage();
    },
    enabled: !!query.hasNextPage,
  });

  return (
    <>
      <PageHeader
        title="Sessions"
        description="Session-level audit trail — every agent conversation, fully chained."
      />

      <div className="grid gap-6 lg:grid-cols-[360px_1fr] items-start">
        {/* Session list */}
        <div className="max-h-[calc(100vh-200px)] overflow-auto pr-2">
          <div className="relative mb-2">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter sessions…"
              className="h-8 pl-7 text-xs"
            />
          </div>
          <div className="mb-3 grid grid-cols-2 gap-2">
            <Select value={window} onValueChange={(v) => setWindow(v as StatsWindow)}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATS_WINDOWS.map((w) => (
                  <SelectItem key={w} value={w}>{WINDOW_LABEL[w]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sort} onValueChange={(v) => setSort(v as SessionSort)}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SESSION_SORTS.map((s) => (
                  <SelectItem key={s} value={s}>{SORT_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={hasAlert} onValueChange={(v) => setHasAlert(v as HasAlertFilter)}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["any", "yes", "no"] as HasAlertFilter[]).map((v) => (
                  <SelectItem key={v} value={v}>{HAS_ALERT_LABEL[v]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={agentFilter} onValueChange={setAgentFilter}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="All agents" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All agents</SelectItem>
                {agentOptions.map((a) => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {query.isLoading && items.length === 0 ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {items.length === 0 ? "No sessions yet." : "No sessions match."}
            </p>
          ) : (
            filtered.map((s) => (
              <SessionListItem
                key={s.session_id}
                s={s}
                isActive={s.session_id === sessionId}
                alertCount={alertCounts.get(s.session_id) ?? 0}
              />
            ))
          )}
          <div ref={sentinelRef} className="h-4" />
          {query.isFetchingNextPage && (
            <div className="py-2 text-center text-xs text-muted-foreground">Loading more…</div>
          )}
        </div>

        {/* Detail panel */}
        <Card>
          <CardContent className="p-6">
            {sessionId ? (
              <SessionDetailPanel sessionId={sessionId} />
            ) : (
              <p className="text-sm text-muted-foreground">Select a session to view its entries.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
