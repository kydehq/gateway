import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, CheckCircle, ShieldX, XCircle } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  STATS_WINDOWS,
  useDlpAlert,
  useSession,
  useSessionsInfinite,
  type SessionStatus,
  type StatsWindow,
} from "@/api/queries";
import { formatChainId, formatIncidentId } from "@/lib/serial-ids";
import { useAgentLabel } from "@/hooks/use-agent-label";
import { useEntryRef } from "@/hooks/use-entry-ref";
import { useMe } from "@/hooks/use-me";
import { downloadPdf } from "@/api/client";
import { cn } from "@/lib/utils";
import type { SessionDetail, SessionSummary } from "@/api/types";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { DlpAlertDetail } from "@/components/shared/dlp-alert-detail";
import { StatusBadge } from "@/components/shared/status-badge";

// A "chain" is a session viewed through the lens of its tool-call
// trajectory. The chain status is derived on the backend:
//   blocked  = at least one entry has action_type='policy_block'
//   observed = no block but at least one open DLP alert
//   allowed  = neither
// Step status (BLOCKED | PREVENTED | COMPLETED) is derived per-entry on
// the frontend from action_type + dlp_alerts.

type ChainStatus = SessionStatus; // alias for readability
type StepStatus = "COMPLETED" | "BLOCKED" | "PREVENTED";

interface ChainStep {
  id: number;
  entry_id?: string;
  name: string;
  description: string;
  detail: string;
  status: StepStatus;
  /** Alert IDs raised on this entry — empty for COMPLETED steps. The
   *  first ID is opened when the user clicks a PREVENTED step. */
  alertIds: string[];
}

interface Chain {
  session_id: string;
  serial_id: number | null;
  status: ChainStatus;
  type: string;          // session.intent or fallback
  outcome: string;       // derived: "Blocked at step N" / "Alert raised" / "Completed"
  agentLabel: string;
  totalSteps: number;
  blockedAtStep: number; // 0 when not blocked
  durationSeconds: number;
  recordsAtRisk: number; // sum of DLP findings counts (approx)
  recordsExposed: number;
  firstTime: string;
  lastTime: string;
  steps: ChainStep[];
}

const STATUS_DISPLAY: Record<ChainStatus, string> = {
  blocked: "BLOCKED",
  observed: "OBSERVED",
  allowed: "ALLOWED",
};

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

// Derive per-step status from an entry. policy_block dominates; any DLP
// alert on the entry marks it PREVENTED; otherwise COMPLETED.
function stepStatusFromEntry(entry: SessionDetail["entries"][number]): StepStatus {
  if (entry.action_type === "policy_block") return "BLOCKED";
  if ((entry.dlp_alerts?.length ?? 0) > 0) return "PREVENTED";
  return "COMPLETED";
}

function stepNameFromEntry(entry: SessionDetail["entries"][number]): string {
  if (entry.action_type === "tool_call" && entry.tool_calls?.length) {
    return entry.tool_calls[0].function ?? "Tool Call";
  }
  if (entry.action_type === "policy_block") return "Policy Block";
  if (entry.action_type === "chat") return "Chat";
  return entry.action_type || "Step";
}

function stepDetailFromEntry(entry: SessionDetail["entries"][number]): string {
  if (entry.action_type === "tool_call" && entry.tool_calls?.length) {
    return entry.tool_calls
      .map((tc) => tc.function ?? "?")
      .slice(0, 3)
      .join(", ");
  }
  return entry.model || "";
}

// Compose Chain from session summary + detail. The summary alone covers
// the list-view; detail fills in the step list and refined fields.
function buildChain(
  summary: SessionSummary,
  detail: SessionDetail | undefined,
  agentShort: (id: string) => string,
): Chain {
  const agentLabel = summary.agents?.[0]
    ? agentShort(summary.agents[0])
    : "Unknown Agent";
  const status = (summary.status ?? "allowed") as ChainStatus;

  const entries = detail?.entries ?? [];
  // Order chronologically — detail returns ASC by seq.
  const steps: ChainStep[] = entries.map((e) => ({
    id: e.seq,
    entry_id: e.entry_id,
    name: stepNameFromEntry(e),
    description: e.why_last ?? e.action_type,
    detail: stepDetailFromEntry(e),
    status: stepStatusFromEntry(e),
    alertIds: (e.dlp_alerts ?? []).map((a) => a.alert_id),
  }));

  const blockedIdx = steps.findIndex((s) => s.status === "BLOCKED");
  const totalSteps = steps.length || summary.entry_count;
  const blockedAtStep = blockedIdx >= 0 ? blockedIdx + 1 : 0;

  // recordsAtRisk: approximate by count of DLP findings across entries.
  // We don't have a true "records" metric — this surfaces "how much was
  // flagged" so the KPI is useful even if not a literal count.
  let alertsObserved = 0;
  for (const e of entries) alertsObserved += e.dlp_alerts?.length ?? 0;

  const outcome =
    status === "blocked"
      ? `Blocked at step ${blockedAtStep || "?"}`
      : status === "observed"
        ? `Alert${alertsObserved === 1 ? "" : "s"} raised (${alertsObserved})`
        : "Completed";

  const type = summary.intent ? summary.intent.replace(/_/g, " ") : "Untitled Session";

  return {
    session_id: summary.session_id,
    serial_id: summary.serial_id ?? null,
    status,
    type,
    outcome,
    agentLabel,
    totalSteps,
    blockedAtStep,
    durationSeconds: summary.duration_seconds ?? 0,
    recordsAtRisk: alertsObserved,
    recordsExposed: status === "blocked" ? 0 : alertsObserved,
    firstTime: summary.first_time,
    lastTime: summary.last_time,
    steps,
  };
}

async function exportIncidentReport(chain: Chain) {
  try {
    await downloadPdf(
      "/api/export/incident-report",
      {
        chain_label: `${chain.type} — ${formatChainId(chain.serial_id ?? 0)}`,
        status: STATUS_DISPLAY[chain.status],
        incident_serial: formatIncidentId(chain.serial_id ?? 0),
        steps: chain.steps.map((s) => ({
          label: s.name,
          status: s.status,
          agent_id: chain.agentLabel,
          dt: chain.lastTime,
        })),
        notes: chain.outcome,
      },
      `incident-${chain.serial_id ?? chain.session_id}.pdf`,
    );
    toast.success("Incident report downloaded");
  } catch (err) {
    toast.error((err as Error).message || "Export failed");
  }
}

// "incidents" is a synthetic chip that bundles blocked + observed — the
// page's default forensic view. The other three map 1:1 to chain status.
type RecentFilter = "incidents" | "all" | ChainStatus;

const WINDOW_LABEL: Record<StatsWindow, string> = {
  "1h": "Last 1h",
  "24h": "Last 24h",
  "7d": "Last 7d",
  "30d": "Last 30d",
  "90d": "Last 90d",
  all: "All time",
};

export default function AgentChainsPage() {
  const { isAdmin, isAuditor } = useMe();
  // Selected DLP alert id — set when the user clicks a PREVENTED step
  // card; opens the shared alert detail sheet.
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const alertQuery = useDlpAlert(selectedAlertId);
  const { shortLabel: agentShort } = useAgentLabel();
  // Open the global entry-detail dialog (rendered in app-shell) for
  // benign / blocked steps that don't have a DLP alert to surface.
  const { open: openEntry } = useEntryRef();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // ?agent=… pre-filters the list to that agent's chains. Other pages
  // (Threats & Alerts row action, etc.) route here with this param.
  const urlAgent = searchParams.get("agent") ?? "";

  const [window, setWindow] = useState<StatsWindow>(urlAgent ? "all" : "30d");
  // Default to "incidents" — the page is incident-shaped per the briefing.
  // If we arrived via ?agent= the user probably wants to see everything
  // from that agent, not just incidents, so default to "all".
  const [recentFilter, setRecentFilter] = useState<RecentFilter>(
    urlAgent ? "all" : "incidents",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [disposition, setDisposition] = useState("");
  const [notes, setNotes] = useState("");

  // Map chip → backend status set.
  //   incidents → blocked + observed
  //   all       → no filter (every status)
  //   blocked / observed / allowed → that single status
  const statusForApi: SessionStatus[] = useMemo(() => {
    if (recentFilter === "incidents") return ["blocked", "observed"];
    if (recentFilter === "all") return [];
    return [recentFilter];
  }, [recentFilter]);

  const query = useSessionsInfinite({
    window,
    has_alert: "any",
    agents: urlAgent ? [urlAgent] : [],
    sort: "newest",
    status: statusForApi,
  });
  const pages = query.data?.pages ?? [];
  const sessions = useMemo(() => pages.flatMap((p) => p.items), [pages]);

  // Auto-select the most recent chain when none is picked, or when the
  // currently-selected one falls out of the filtered set.
  useEffect(() => {
    if (sessions.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!selectedId || !sessions.find((s) => s.session_id === selectedId)) {
      setSelectedId(sessions[0].session_id);
    }
  }, [sessions, selectedId]);

  const selectedSummary = useMemo(
    () => sessions.find((s) => s.session_id === selectedId) ?? null,
    [sessions, selectedId],
  );
  const { data: selectedDetail, isLoading: detailLoading } = useSession(selectedId);

  const chain = useMemo(
    () => (selectedSummary ? buildChain(selectedSummary, selectedDetail, agentShort) : null),
    [selectedSummary, selectedDetail, agentShort],
  );

  const recentChains = useMemo(
    () => sessions.map((s) => buildChain(s, undefined, agentShort)),
    [sessions, agentShort],
  );

  const RECENT_FILTERS: { key: RecentFilter; label: string }[] = [
    { key: "incidents", label: "Incidents" },
    { key: "blocked",   label: "Blocked" },
    { key: "observed",  label: "Observed" },
    { key: "allowed",   label: "Allowed" },
    { key: "all",       label: "All" },
  ];

  const isLoading = query.isLoading;

  return (
    <>
      <PageHeader
        title="Agent Chains"
        description="Multi-step action sequences — observed agent activity."
        actions={
          <Select value={window} onValueChange={(v) => setWindow(v as StatsWindow)}>
            <SelectTrigger className="h-9 w-36 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATS_WINDOWS.map((w) => (
                <SelectItem key={w} value={w}>{WINDOW_LABEL[w]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />

      {urlAgent && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
          <span className="font-mono text-xs">
            Filtered to agent <strong>{urlAgent}</strong>
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={() => setSearchParams(new URLSearchParams())}
          >
            <XCircle className="h-3 w-3" /> Clear
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : sessions.length === 0 ? (
        <div className="rounded-md border bg-card p-12 text-center text-sm text-muted-foreground">
          <p className="font-semibold text-foreground mb-1">No agent chains in the selected window.</p>
          <p>Switch to a wider window or change the filter chips below to see ALLOWED sessions.</p>
        </div>
      ) : !chain ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          {/* Detail banner: neutral card, no colored wash. The
              status badge stays on the neutral/status axis; a red accent badge
              appears top-right ONLY for real severity (alerts raised). A blue
              left-border marks "actively observed". A full red wash is reserved
              for a genuine block (critical incident). */}
          <div
            className={cn(
              "mb-7 rounded-md border p-5",
              chain.status === "blocked"
                ? "border-sev-critical/20 bg-sev-critical/5"
                : "border-border bg-card",
              chain.status === "observed" ? "border-l-[3px] border-l-status-active" : "",
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <StatusBadge status={STATUS_DISPLAY[chain.status]} />
                <h2 className="mt-2 text-[17px] font-semibold text-foreground">
                  {chain.type} — {chain.outcome}
                </h2>
                <div className="mt-1 font-mono text-xs text-muted-foreground">
                  {chain.agentLabel} · {chain.lastTime} · Chain ID: {formatChainId(chain.serial_id ?? 0)}
                </div>
              </div>
              {chain.recordsAtRisk > 0 ? (
                <span className="shrink-0 inline-flex items-center rounded-[5px] border border-sev-critical/20 bg-sev-critical/10 px-2 py-[3px] font-mono text-[11px] font-semibold uppercase tracking-[0.06em] text-sev-critical">
                  {chain.recordsAtRisk} alert{chain.recordsAtRisk === 1 ? "" : "s"} raised
                </span>
              ) : null}
            </div>
          </div>

          {/* KPIs */}
          <div className="grid grid-cols-5 gap-3 mb-7">
            <div className="rounded-lg border p-4 text-center">
              <div className="text-4xl font-bold text-foreground">{chain.recordsExposed}</div>
              <div className="text-xs font-semibold text-muted-foreground mt-1">DLP Findings (exposed)</div>
            </div>
            <MetricCard label="DLP Findings (flagged)" value={chain.recordsAtRisk} />
            <MetricCard
              label="Blocked at Step"
              value={chain.blockedAtStep > 0 ? `${chain.blockedAtStep} / ${chain.totalSteps}` : "—"}
            />
            <MetricCard label="Chain Duration" value={formatDuration(chain.durationSeconds)} />
            <MetricCard label="Data Integrity" value="VERIFIED" tone="success" />
          </div>

          {/* Step visualizer */}
          <div className="rounded-lg border bg-card p-6 mb-7 overflow-x-auto">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-4">
              Action Chain {detailLoading ? "(loading…)" : ""}
            </p>
            {chain.steps.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">
                {detailLoading ? "Loading entries…" : "No entries in this session."}
              </p>
            ) : (
              <div className="flex items-start gap-0 min-w-max">
                {chain.steps.map((step, idx) => {
                  const isPrevented = step.status === "PREVENTED";
                  const isBlocked = step.status === "BLOCKED";
                  const next = chain.steps[idx + 1];
                  const firstAlertId = step.alertIds[0];
                  // Alert sheet takes precedence — DLP findings are the more
                  // forensically-interesting view. Steps without an alert
                  // open the generic entry-detail dialog instead.
                  const opensAlert = isPrevented && !!firstAlertId;
                  const opensEntry = !opensAlert;
                  const cardClassName = cn(
                    "rounded-lg border p-4 w-48 text-left transition-opacity cursor-pointer",
                    isBlocked
                      ? "border-sev-critical/30 bg-sev-critical/5 hover:border-sev-critical/50 hover:bg-sev-critical/10"
                      : "border-border bg-background hover:border-foreground/40 hover:bg-accent/40",
                    opensAlert ? "hover:border-sev-medium/40 hover:bg-sev-medium/10" : "",
                    isPrevented ? "opacity-90 hover:opacity-100" : "",
                  );
                  const cardInner = (
                    <>
                      <div className="flex items-center gap-2 mb-2">
                        {step.status === "COMPLETED" && <CheckCircle className="h-4 w-4 text-brand-green shrink-0" />}
                        {step.status === "BLOCKED"   && <ShieldX className="h-4 w-4 text-sev-critical shrink-0" />}
                        {step.status === "PREVENTED" && <XCircle className="h-4 w-4 text-sev-medium shrink-0" />}
                        <span className="text-xs font-semibold leading-tight">{step.name}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mb-2 leading-tight line-clamp-2">{step.description}</p>
                      <p className="font-mono text-[10px] text-muted-foreground/70 leading-tight truncate">{step.detail}</p>
                      {isPrevented && opensAlert && (
                        <p className="mt-2 text-[10px] font-semibold text-sev-medium uppercase tracking-wide">
                          FLAGGED — DLP alert raised
                          <span className="ml-1 normal-case text-muted-foreground/80 font-normal">
                            · click for details
                          </span>
                        </p>
                      )}
                      {isBlocked && (
                        <span className="mt-2 inline-block text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-sev-critical/10 text-sev-critical border border-sev-critical/20">
                          BLOCKED
                        </span>
                      )}
                    </>
                  );
                  const onClick = () => {
                    if (opensAlert) {
                      setSelectedAlertId(firstAlertId);
                    } else if (opensEntry) {
                      openEntry(String(step.id));
                    }
                  };
                  return (
                    <div key={step.id} className="flex items-start">
                      <button
                        type="button"
                        className={cardClassName}
                        onClick={onClick}
                        title={opensAlert ? "Open alert details" : "Open message details"}
                      >
                        {cardInner}
                      </button>
                      {next && (
                        <div className="flex flex-col items-center justify-start pt-6 px-1">
                          <ArrowRight className="h-4 w-4 text-muted-foreground" />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Role-specific actions */}
          <div className="rounded-lg border p-5 mb-8">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-4">Actions</p>
            {isAdmin ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => toast.success("Chain acknowledged")}>Acknowledge</Button>
                <Button variant="outline" size="sm" onClick={() => toast.info("Policy editor coming in next release")}>Add to Policy</Button>
                <Button variant="outline" size="sm" onClick={() => exportIncidentReport(chain)}>🛡 Export for Incident Report</Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground"
                  disabled={!chain.agentLabel}
                  onClick={() => {
                    // Route to /agent-chains?agent=<id>. We use the raw
                    // agent_id from the session's agents[] array so the
                    // backend filter matches; the label is for display.
                    const rawAgent =
                      selectedSummary?.agents?.[0] ?? chain.agentLabel;
                    navigate(`/agent-chains?agent=${encodeURIComponent(rawAgent)}`);
                  }}
                >
                  Show all chains from this agent →
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-3 max-w-sm">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">Disposition</label>
                  <Select value={disposition} onValueChange={setDisposition}>
                    <SelectTrigger className="h-9">
                      <SelectValue placeholder="Select disposition..." />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="reviewed">Reviewed</SelectItem>
                      <SelectItem value="false_positive">False Positive</SelectItem>
                      <SelectItem value="confirmed_incident">Confirmed Incident</SelectItem>
                      <SelectItem value="escalated">Escalated</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">Auditor Notes</label>
                  <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Add notes..." />
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => exportIncidentReport(chain)}>🛡 Export as Compliance Evidence</Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground"
                    onClick={() => navigate(`/audit-log?session=${encodeURIComponent(chain.session_id)}`)}
                  >
                    Show full audit trail →
                  </Button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Recent chains list with filter chips */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">Recent Agent Chains</h2>
          <div className="flex gap-1">
            {RECENT_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setRecentFilter(f.key)}
                className={cn(
                  "rounded px-3 py-1 text-xs font-medium transition-colors border",
                  recentFilter === f.key
                    ? "bg-foreground text-background border-foreground"
                    : "bg-card text-muted-foreground border-border hover:border-foreground/40",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        {recentChains.length === 0 ? (
          <p className="rounded-md border bg-card py-8 text-center text-sm text-muted-foreground">
            No chains match this filter.
          </p>
        ) : (
          <div className="rounded-md border divide-y">
            {recentChains.map((c) => (
              <div
                key={c.session_id}
                onClick={() => setSelectedId(c.session_id)}
                className={cn(
                  "flex items-center justify-between px-4 py-3 cursor-pointer text-sm hover:bg-accent/40 transition-colors",
                  c.session_id === selectedId ? "bg-accent/60" : "",
                )}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-xs text-muted-foreground shrink-0">
                    {formatChainId(c.serial_id ?? 0)}
                  </span>
                  <span className="text-muted-foreground shrink-0">·</span>
                  <span className="font-medium shrink-0">{c.agentLabel}</span>
                  <span className="text-muted-foreground shrink-0">·</span>
                  <span className="text-muted-foreground truncate">{c.type}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0 ml-4">
                  <StatusBadge status={STATUS_DISPLAY[c.status]} />
                  <span className="text-xs text-muted-foreground">{c.totalSteps} entries</span>
                  <span className="text-xs text-muted-foreground">{c.lastTime}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Sheet
        open={!!selectedAlertId}
        onOpenChange={(o) => !o && setSelectedAlertId(null)}
      >
        <SheetContent className="w-[480px] sm:max-w-[480px] overflow-y-auto">
          {alertQuery.isLoading && (
            <p className="text-sm text-muted-foreground py-6 text-center">Loading…</p>
          )}
          {alertQuery.isError && (
            <p className="text-sm text-destructive py-6 text-center">
              Failed to load alert.
            </p>
          )}
          {alertQuery.data && (
            <>
              <DlpAlertDetail
                alert={alertQuery.data}
                isAuditor={isAuditor}
                onEntityLinkClick={() => setSelectedAlertId(null)}
              />
              <div className="mt-6 flex justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSelectedAlertId(null);
                    navigate("/threats-alerts");
                  }}
                >
                  Open in Threats →
                </Button>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
