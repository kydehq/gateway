import { useEffect, useState } from "react";
import { Shield } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useNavigate, useParams } from "react-router-dom";
import { useBlockAgent, useDlpAlert, useDlpAlerts, useTogglePolicy, useTransitionDlpAlert } from "@/api/queries";
import { downloadPdf } from "@/api/client";
import { AllowlistButton } from "@/components/shared/allowlist-button";
import { formatAlertId } from "@/lib/serial-ids";
import { useAgentLabel } from "@/hooks/use-agent-label";
import { useMe } from "@/hooks/use-me";
import { useFeatures } from "@/hooks/use-features";
import { PaidLock } from "@/components/shared/upgrade-lock";
import { cn } from "@/lib/utils";
import type { DlpAlert, DlpStatus, DlpDisposition } from "@/api/types";
import {
  DlpAlertDetail,
  SEV_STYLE,
  getAlertType,
  getSeverity,
} from "@/components/shared/dlp-alert-detail";
import { DlpEventTimeline } from "@/components/shared/dlp-event-timeline";
import { StatusBadge } from "@/components/shared/status-badge";

// Explicit (not constructed) class strings so Tailwind's JIT emits them.
const SEV_DOT: Record<"CRITICAL" | "HIGH" | "MEDIUM" | "LOW", string> = {
  CRITICAL: "bg-sev-critical",
  HIGH:     "bg-sev-high",
  MEDIUM:   "bg-sev-medium",
  LOW:      "bg-sev-low",
};
const SEV_TEXT: Record<"CRITICAL" | "HIGH" | "MEDIUM" | "LOW", string> = {
  CRITICAL: "text-sev-critical",
  HIGH:     "text-sev-high",
  MEDIUM:   "text-sev-medium",
  LOW:      "text-sev-low",
};

function mapStatus(s: string) {
  if (s === "new") return "New";
  if (s === "in_review") return "In Review";
  if (s === "escalated") return "Escalated";
  if (s === "closed") return "Closed";
  return s;
}

type Filter = "open" | "in_review" | "escalated" | "closed" | "all";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "in_review", label: "In Review" },
  { key: "escalated", label: "Escalated" },
  { key: "closed", label: "Closed" },
  { key: "all", label: "All" },
];

type SourceFilter = "all" | "chat" | "mcp";

const SOURCE_FILTERS: { key: SourceFilter; label: string }[] = [
  { key: "all", label: "All sources" },
  { key: "chat", label: "Chat" },
  { key: "mcp", label: "MCP" },
];

type BulkAction = "close_fp" | "close_leak" | "assign_me" | "export";

const BULK_LABELS: Record<BulkAction, { verb: string; description: string }> = {
  close_fp: {
    verb: "Close as false positive",
    description: "Set status to closed with disposition='false_positive'. This cannot be undone via the UI.",
  },
  close_leak: {
    verb: "Close as confirmed leak",
    description: "Set status to closed with disposition='confirmed_leak'. This cannot be undone via the UI.",
  },
  assign_me: {
    verb: "Assign to me",
    description: "Claim the selected alerts. Status moves to 'in_review' if currently 'new'.",
  },
  export: {
    verb: "Export as evidence (PDF)",
    description: "Download one signed PDF per alert. Your browser will prompt for each file.",
  },
};

export default function ThreatsAlertsPage() {
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const { data: alerts = [], isLoading } = useDlpAlerts(
    sourceFilter === "all" ? null : sourceFilter,
  );
  const { isAdmin, isAuditor, me } = useMe();
  const { enforcementEnabled } = useFeatures();
  const { shortLabel: agentShort } = useAgentLabel();
  const transition = useTransitionDlpAlert();
  const blockAgent = useBlockAgent();
  const togglePolicy = useTogglePolicy();
  const navigate = useNavigate();
  // Deep-link entry: email alert links route to /alerts/:alertId, which
  // mounts this page with the param set. We fetch that one alert directly
  // (not waiting on the list query) and open the detail sheet.
  const { alertId: routeAlertId } = useParams<{ alertId: string }>();
  const { data: deepLinkAlert, error: deepLinkError } = useDlpAlert(routeAlertId ?? null);
  const [filter, setFilter] = useState<Filter>("open");
  const [selected, setSelected] = useState<DlpAlert | null>(null);

  useEffect(() => {
    if (deepLinkAlert) setSelected(deepLinkAlert);
  }, [deepLinkAlert]);
  useEffect(() => {
    if (deepLinkError) toast.error("Alert not found");
  }, [deepLinkError]);

  // Programmatic dismissals (transition success, policy disable, …) bypass
  // the Sheet's onOpenChange. Centralising the close keeps the URL in sync
  // with the sheet — otherwise a refetched deep-link alert would reopen.
  function closeSheet() {
    setSelected(null);
    if (routeAlertId) navigate("/threats-alerts", { replace: true });
  }
  const [notes, setNotes] = useState("");
  // Escalation is the one transition that requires a note, so the click
  // opens a confirm dialog with an inline reason field instead of firing
  // immediately. The note becomes the `note` field on the audit-event row.
  const [escalateNote, setEscalateNote] = useState("");
  const [escalateOpen, setEscalateOpen] = useState(false);
  // Bulk-checkbox state. A Set so toggling is O(1) and the toolbar
  // condition is just selectedIds.size > 0.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [bulkInProgress, setBulkInProgress] = useState(false);

  const filtered = alerts.filter((a) => {
    if (filter === "open") return a.status !== "closed";
    if (filter === "in_review") return a.status === "in_review";
    if (filter === "escalated") return a.status === "escalated";
    if (filter === "closed") return a.status === "closed";
    return true;
  });

  // Chips reflect current posture — closed alerts are handled, not
  // active threats. Switch to the Closed tab to audit past dispositions.
  const counts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
  for (const a of alerts) {
    if (a.status === "closed") continue;
    counts[getSeverity(a)]++;
  }

  function doTransition(
    alert: DlpAlert,
    to_status: DlpStatus,
    disposition?: DlpDisposition,
    note?: string,
  ) {
    transition.mutate(
      { alert_id: String(alert.alert_id ?? alert.id), to_status, disposition, note },
      { onSuccess: () => { toast.success("Status updated"); closeSheet(); } },
    );
  }

  // Map an alert to the canonical string ID used by the transition endpoint.
  const idOf = (a: DlpAlert) => String(a.alert_id ?? a.id);
  const allVisibleIds = filtered.map(idOf);
  const allChecked =
    allVisibleIds.length > 0 && allVisibleIds.every((id) => selectedIds.has(id));
  const someChecked =
    !allChecked && allVisibleIds.some((id) => selectedIds.has(id));

  function toggleOne(id: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAllVisible(checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) for (const id of allVisibleIds) next.add(id);
      else for (const id of allVisibleIds) next.delete(id);
      return next;
    });
  }

  async function runBulk(action: BulkAction) {
    // Snapshot the selection so concurrent UI changes don't drift mid-run.
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setBulkInProgress(true);
    const succeeded: string[] = [];
    const failed: string[] = [];

    for (const id of ids) {
      try {
        if (action === "close_fp") {
          await transition.mutateAsync({
            alert_id: id, to_status: "closed", disposition: "false_positive",
          });
        } else if (action === "close_leak") {
          await transition.mutateAsync({
            alert_id: id, to_status: "closed", disposition: "confirmed_leak",
          });
        } else if (action === "assign_me") {
          await transition.mutateAsync({
            alert_id: id,
            to_status: "in_review",
            assignee_id: me?.user_id ? Number(me.user_id) : null,
          });
        } else {
          // export: serial PDF downloads. Per-file because the browser
          // download flow needs the click context — looping is fine for
          // small selections; large selections give multiple prompts.
          await downloadPdf(
            "/api/export/compliance-evidence",
            { kind: "alert", id },
            `alert-${id}.pdf`,
          );
        }
        succeeded.push(id);
      } catch (err) {
        failed.push(id);
        console.error(`bulk ${action} failed for ${id}:`, err);
      }
    }

    // Clear successful IDs from selection so failures stay highlighted
    // for the user to retry.
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of succeeded) next.delete(id);
      return next;
    });

    const { verb } = BULK_LABELS[action];
    if (failed.length === 0) {
      toast.success(`${verb}: ${succeeded.length} alert${succeeded.length === 1 ? "" : "s"}`);
    } else if (succeeded.length === 0) {
      toast.error(`${verb} failed for all ${failed.length} alerts. Check console.`);
    } else {
      toast.warning(
        `${verb}: ${succeeded.length} succeeded, ${failed.length} failed. Failed rows stay selected.`,
      );
    }
    setBulkInProgress(false);
    setBulkAction(null);
  }

  return (
    <>
      <PageHeader
        title="Threats & Alerts"
        description="All active security findings — what happened, how critical, what action."
      />

      {/* Neutral stat cards (spec §7): big neutral-black count, with the
          severity carried only by a small dot + label — not a pastel fill. */}
      <div className="grid grid-cols-4 gap-3 mb-7">
        {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((sev) => (
          <div key={sev} className="flex flex-col gap-3.5 rounded-md border border-border bg-card p-5">
            <div className="flex items-center gap-2">
              <span className={cn("h-2 w-2 shrink-0 rounded-full", SEV_DOT[sev])} />
              <span className={cn("eyebrow", SEV_TEXT[sev])}>{sev}</span>
            </div>
            <div className="stat-value">{counts[sev]}</div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mb-2">
        {SOURCE_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setSourceFilter(f.key)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium transition-colors border",
              sourceFilter === f.key
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-card text-muted-foreground border-border hover:border-primary/40",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="flex gap-2 mb-4">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={cn(
              "rounded-full px-4 py-1.5 text-sm font-medium transition-colors border",
              filter === f.key
                ? "bg-foreground text-background border-foreground"
                : "bg-card text-muted-foreground border-border hover:border-foreground/40",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Shield className="mb-3 h-10 w-10 text-brand-green" />
          {(() => {
            // Contextual message per filter so the empty state actually
            // tells the user *why* nothing is showing.
            if (alerts.length === 0) {
              return (
                <>
                  <p className="font-semibold text-lg">No alerts yet.</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    DLP scanning is active. New findings will appear here as agents run.
                  </p>
                </>
              );
            }
            if (filter === "open") {
              return (
                <>
                  <p className="font-semibold text-lg">No open alerts.</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    All alerts triaged. Switch to <em>Closed</em> to review past dispositions.
                  </p>
                </>
              );
            }
            if (filter === "in_review") {
              return (
                <>
                  <p className="font-semibold text-lg">Nothing in review.</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    Claim an alert from the <em>Open</em> filter to start triage.
                  </p>
                </>
              );
            }
            if (filter === "escalated") {
              return (
                <>
                  <p className="font-semibold text-lg">Nothing escalated.</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    Escalations land here when an analyst flags an alert for higher-level attention.
                  </p>
                </>
              );
            }
            return (
              <>
                <p className="font-semibold text-lg">No closed alerts yet.</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Once alerts are dispositioned, they land here for audit.
                </p>
              </>
            );
          })()}
        </div>
      ) : (
        <div className="rounded-md border">
          {selectedIds.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b bg-accent/30 px-3 py-2 text-sm">
              <span className="font-semibold">{selectedIds.size} selected</span>
              <span className="text-muted-foreground">·</span>
              <Button size="sm" variant="outline" disabled={bulkInProgress} onClick={() => setBulkAction("close_fp")}>
                Close as False Positive
              </Button>
              <Button size="sm" variant="outline" disabled={bulkInProgress} onClick={() => setBulkAction("close_leak")}>
                Close as Confirmed Leak
              </Button>
              <Button size="sm" variant="outline" disabled={bulkInProgress || !me?.user_id} onClick={() => setBulkAction("assign_me")}>
                Assign to me
              </Button>
              <Button size="sm" variant="outline" disabled={bulkInProgress} onClick={() => setBulkAction("export")}>
                🛡 Export PDFs
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                disabled={bulkInProgress}
                onClick={() => setSelectedIds(new Set())}
              >
                Clear selection
              </Button>
            </div>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={allChecked ? true : someChecked ? "indeterminate" : false}
                    onCheckedChange={(v) => toggleAllVisible(Boolean(v))}
                    aria-label="Select all visible alerts"
                  />
                </TableHead>
                <TableHead className="font-mono text-xs w-28">Alert ID</TableHead>
                <TableHead className="w-28">Severity</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Detected</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right w-28">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((alert) => {
                const sev = getSeverity(alert);
                const id = idOf(alert);
                const isSelected = selectedIds.has(id);
                return (
                  <TableRow
                    key={String(alert.id)}
                    className={cn(
                      "cursor-pointer hover:bg-accent/40",
                      isSelected && "bg-accent/30",
                    )}
                    onClick={() => setSelected(alert)}
                  >
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={(v) => toggleOne(id, Boolean(v))}
                        aria-label={`Select alert ${id}`}
                      />
                    </TableCell>
                    <TableCell className="font-mono text-xs">{formatAlertId(alert.serial_id ?? alert.id)}</TableCell>
                    <TableCell>
                      <span className={cn("inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold", SEV_STYLE[sev])}>
                        {sev}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm">
                      <div className="flex items-center gap-1.5">
                        <span>{getAlertType(alert)}</span>
                        {alert.prevented && (
                          <span
                            className="inline-flex items-center rounded border border-sev-critical/40 bg-sev-critical/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sev-critical"
                            title="The request that raised this alert was blocked inline by DLP prevention"
                          >
                            Prevented
                          </span>
                        )}
                        {alert.source_type === "mcp" && (
                          <span
                            className="inline-flex items-center rounded border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary"
                            title={`MCP · ${alert.mcp_server_name ?? "?"} · ${alert.mcp_method ?? ""}${alert.mcp_tool_name ? ` · ${alert.mcp_tool_name}` : ""}`}
                          >
                            MCP
                            {alert.mcp_server_name ? ` · ${alert.mcp_server_name}` : ""}
                            {alert.mcp_tool_name ? ` · ${alert.mcp_tool_name}` : ""}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground max-w-[140px] truncate">
                      {agentShort(alert.entry_id ?? alert.session_id ?? "unknown")}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <RelativeTime value={alert.created_dt} />
                    </TableCell>
                    <TableCell><StatusBadge status={mapStatus(alert.status)} /></TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {alert.agent_id ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            title={`See agent-chains involving ${alert.agent_id}`}
                            onClick={(e) => {
                              // Until agent-chains is wired to real data,
                              // we pass the agent_id via query so the page
                              // can pre-filter once it lands.
                              e.stopPropagation();
                              navigate(`/agent-chains?agent=${encodeURIComponent(alert.agent_id!)}`);
                            }}
                          >
                            Show chain →
                          </Button>
                        ) : null}
                        <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); setSelected(alert); }}>
                          Details →
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <Sheet open={!!selected} onOpenChange={(o) => !o && closeSheet()}>
        <SheetContent className="w-[816px] sm:max-w-[816px] overflow-y-auto">
          {selected && (
            <>
              <DlpAlertDetail
                alert={selected}
                isAuditor={isAuditor}
                onEntityLinkClick={() => closeSheet()}
              />
              {selected.source_type === "mcp" && (
                <section className="mt-4 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs">
                  <p className="font-semibold uppercase tracking-wider text-primary">MCP source</p>
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-muted-foreground">
                    <dt>Server</dt>
                    <dd className="font-mono text-foreground">{selected.mcp_server_name ?? "—"}</dd>
                    <dt>Method</dt>
                    <dd className="font-mono text-foreground">{selected.mcp_method ?? "—"}</dd>
                    {selected.mcp_tool_name && (
                      <>
                        <dt>Tool</dt>
                        <dd className="font-mono text-foreground">{selected.mcp_tool_name}</dd>
                      </>
                    )}
                  </dl>
                </section>
              )}
              <div className="space-y-6 text-sm mt-6">
                <section>
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Actions</p>
                  <div className="flex flex-col gap-2">
                    {/* Start Review: only meaningful when the alert is fresh.
                        Hidden once it's already in_review/escalated/closed
                        so the action list reflects the current state. */}
                    {selected.status === "new" && (
                      <Button variant="outline" size="sm" disabled={transition.isPending} onClick={() => doTransition(selected, "in_review")}>
                        Start Review
                      </Button>
                    )}
                    {/* Escalate: visible while still open + not yet escalated.
                        Opens a confirm dialog that captures the reason note. */}
                    {(selected.status === "new" || selected.status === "in_review") && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="border-sev-high/40 text-sev-high hover:bg-sev-high/10"
                        disabled={transition.isPending}
                        onClick={() => { setEscalateNote(""); setEscalateOpen(true); }}
                      >
                        Escalate
                      </Button>
                    )}
                    {/* De-escalate: only meaningful from escalated. Drops the
                        alert back into the normal review queue without
                        closing it — for "the escalation was a false alarm,
                        not the alert itself". */}
                    {selected.status === "escalated" && (
                      <Button variant="outline" size="sm" disabled={transition.isPending} onClick={() => doTransition(selected, "in_review")}>
                        De-escalate
                      </Button>
                    )}
                    {/* Close-with-disposition buttons stay visible for any
                        open state — escalated alerts close into a verdict
                        directly, no re-review step needed. */}
                    {selected.status !== "closed" && (
                      <>
                        <Button variant="outline" size="sm" disabled={transition.isPending} onClick={() => doTransition(selected, "closed", "false_positive")}>
                          False Positive
                        </Button>
                        <Button variant="outline" size="sm" disabled={transition.isPending} onClick={() => doTransition(selected, "closed", "confirmed_leak")}>
                          Confirm Incident
                        </Button>
                      </>
                    )}
                    {(isAdmin || isAuditor) && (() => {
                      // Per-pattern mute. Available to admins AND auditors:
                      // auditors are the role actually triaging the alert
                      // queue, so they need the lever themselves rather
                      // than handing off to an admin every time a regex
                      // gets noisy.
                      if (selected.scanner !== "regex") return null;
                      type Finding = { pattern_id?: string; pattern_name?: string };
                      const findings = (
                        selected as DlpAlert & { findings_parsed?: Finding[] }
                      ).findings_parsed;
                      const withId = (findings ?? []).filter((f) => f.pattern_id);
                      if (withId.length === 0) return null;
                      const unique = Array.from(
                        new Map(withId.map((f) => [f.pattern_id!, f])).values(),
                      );
                      const disable = async (f: Finding) => {
                        if (!f.pattern_id) return;
                        const label = f.pattern_name || f.pattern_id;
                        if (!window.confirm(
                          `Disable "${label}" gateway-wide? The gateway will stop creating alerts for this pattern until you re-enable it from the Policies page.`,
                        )) return;
                        try {
                          await togglePolicy.mutateAsync({ id: f.pattern_id, enabled: false });
                          toast.success(
                            `Disabled ${label} — gateway will stop creating alerts for this pattern.`,
                          );
                          closeSheet();
                        } catch (err) {
                          toast.error((err as Error).message || "Disable failed");
                        }
                      };
                      if (unique.length === 1) {
                        const only = unique[0];
                        const label = only.pattern_name || only.pattern_id;
                        return (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={togglePolicy.isPending}
                            title="Global per-pattern action; affects all future alerts."
                            onClick={() => disable(only)}
                          >
                            Disable "{label}"
                          </Button>
                        );
                      }
                      return (
                        <div className="flex flex-col gap-1 rounded-md border border-border p-2">
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                            Disable a policy
                          </p>
                          {unique.map((f) => (
                            <Button
                              key={f.pattern_id}
                              variant="outline"
                              size="sm"
                              disabled={togglePolicy.isPending}
                              title="Global per-pattern action; affects all future alerts."
                              onClick={() => disable(f)}
                            >
                              {f.pattern_name || f.pattern_id}
                            </Button>
                          ))}
                        </div>
                      );
                    })()}
                    {isAdmin && (
                      <>
                        {(() => {
                          // "Add to Policy" → allowlist the first finding on the
                          // selected alert. Reuses the existing dialog component
                          // so the admin gets a consistent UX with the DLP
                          // detail panel.
                          const findings = (selected as DlpAlert & { findings_parsed?: Array<{ entity_type?: string; label?: string; type?: string; text?: string; match?: string }> }).findings_parsed;
                          const first = findings && findings[0];
                          const entityType = first?.entity_type ?? first?.label ?? first?.type;
                          if (!entityType) {
                            return (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled
                                title="No findings on this alert to allowlist"
                              >
                                Add to Policy
                              </Button>
                            );
                          }
                          return (
                            <AllowlistButton
                              scanner={selected.scanner}
                              entityType={String(entityType)}
                              matchText={first?.text ?? first?.match ?? null}
                            />
                          );
                        })()}
                        <PaidLock
                          locked={!enforcementEnabled}
                          hint="Agent blocking is part of enforcement — available in the KYDE Enterprise edition. The sandbox edition is observe-only."
                        >
                          <Button
                            variant="outline"
                            size="sm"
                            className="border-destructive text-destructive hover:bg-destructive/10"
                            disabled={!selected.agent_id || blockAgent.isPending}
                            title={selected.agent_id ? `Block ${selected.agent_id}` : "No agent on this alert"}
                            onClick={async () => {
                              if (!selected.agent_id) {
                                toast.error("No agent on this alert");
                                return;
                              }
                              if (!window.confirm(
                                `Block ${selected.agent_id}? All future proxy requests from this agent will be rejected with 403.`,
                              )) return;
                              try {
                                await blockAgent.mutateAsync({
                                  agent_id: selected.agent_id,
                                  reason: `Blocked from alert ${selected.alert_id ?? selected.id}`,
                                });
                                toast.success(`Agent ${selected.agent_id} blocked`);
                              } catch (err) {
                                toast.error((err as Error).message || "Block failed");
                              }
                            }}
                          >
                            Block Agent
                          </Button>
                        </PaidLock>
                      </>
                    )}
                    {!isAdmin && (
                      <>
                        <label className="block mt-2">
                          <span className="text-xs text-muted-foreground mb-1 block">Auditor Notes</span>
                          <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Add notes for the audit record..." className="text-sm" />
                        </label>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={async () => {
                            try {
                              await downloadPdf(
                                "/api/export/compliance-evidence",
                                { kind: "alert", id: selected.alert_id ?? String(selected.id) },
                                `alert-${selected.alert_id ?? selected.id}.pdf`,
                              );
                              toast.success("Evidence downloaded");
                            } catch (err) {
                              toast.error((err as Error).message || "Export failed");
                            }
                          }}
                        >
                          🛡 Export as Evidence
                        </Button>
                      </>
                    )}
                  </div>
                </section>

                <section>
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                    Triage Events
                  </p>
                  <DlpEventTimeline alertId={String(selected.alert_id ?? selected.id)} />
                </section>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <Dialog open={bulkAction !== null} onOpenChange={(o) => !o && setBulkAction(null)}>
        <DialogContent className="max-w-md">
          {bulkAction !== null && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {BULK_LABELS[bulkAction].verb} — {selectedIds.size} alert
                  {selectedIds.size === 1 ? "" : "s"}?
                </DialogTitle>
                <DialogDescription>
                  {BULK_LABELS[bulkAction].description}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setBulkAction(null)} disabled={bulkInProgress}>
                  Cancel
                </Button>
                <Button onClick={() => runBulk(bulkAction)} disabled={bulkInProgress}>
                  {bulkInProgress ? "Working…" : "Confirm"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={escalateOpen} onOpenChange={(o) => !o && setEscalateOpen(false)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Escalate alert</DialogTitle>
            <DialogDescription>
              Escalation flags this alert for higher-level attention. The reason
              you give here is recorded on the audit trail and surfaces in the
              event timeline.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <label className="text-xs text-muted-foreground mb-1 block" htmlFor="escalate-note">
              Reason
            </label>
            <Input
              id="escalate-note"
              value={escalateNote}
              onChange={(e) => setEscalateNote(e.target.value)}
              placeholder="Why is this being escalated?"
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setEscalateOpen(false)}
              disabled={transition.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                if (!selected) return;
                if (!escalateNote.trim()) {
                  toast.error("Please add a reason for the escalation.");
                  return;
                }
                doTransition(selected, "escalated", undefined, escalateNote.trim());
                setEscalateOpen(false);
              }}
              disabled={transition.isPending || !escalateNote.trim()}
            >
              {transition.isPending ? "Escalating…" : "Escalate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
