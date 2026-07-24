import { useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { ActionBadge } from "./action-badge";
import { CopyButton } from "./copy-button";
import { useEntry } from "@/api/queries";
import { useEntryRef } from "@/hooks/use-entry-ref";
import { useMe } from "@/hooks/use-me";
import { ChevronLeft, ChevronRight, Link as LinkIcon, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { formatAlertId } from "@/lib/serial-ids";
import {
  DlpAlertDetail,
  SEV_STYLE,
  getAlertType,
  getSeverity,
  REDACTED_PLACEHOLDER,
} from "./dlp-alert-detail";
import type { DlpAlert, DlpFinding, EntryDetail } from "@/api/types";
import type { ReactNode } from "react";

function Field({ label, value, copyable }: { label: string; value: ReactNode; copyable?: string }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] font-mono text-muted-foreground uppercase tracking-wide">
        {label}
      </div>
      <div className="flex items-center gap-1">
        <div className="text-[13px] text-foreground break-all flex-1">{value ?? "-"}</div>
        {copyable ? <CopyButton value={copyable} label={label.toLowerCase()} /> : null}
      </div>
    </div>
  );
}

function CodeBlock({ children }: { children: ReactNode }) {
  return (
    <pre className="bg-muted/40 border border-border rounded-md px-4 py-3 font-mono text-xs text-muted-foreground overflow-x-auto whitespace-pre leading-relaxed">
      {children}
    </pre>
  );
}

// Locate the first alert whose finding's matched_value appears in
// `content`. Returns null when nothing matches — non-auditor messages
// always return null because the matched_value is replaced server-side
// with the redaction placeholder.
function findFlaggingAlert(content: unknown, alerts: DlpAlert[]): DlpAlert | null {
  const text = typeof content === "string" ? content : String(content ?? "");
  if (!text) return null;
  for (const alert of alerts) {
    const findings = parseAlertFindings(alert);
    for (const f of findings) {
      const m = f.matched_value;
      if (m && m !== REDACTED_PLACEHOLDER && text.includes(m)) return alert;
    }
  }
  return null;
}

function parseAlertFindings(alert: DlpAlert): DlpFinding[] {
  const raw = alert.findings_parsed ?? alert.findings;
  if (!raw) return [];
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed as DlpFinding[]) : [];
    } catch {
      return [];
    }
  }
  return Array.isArray(raw) ? (raw as DlpFinding[]) : [];
}

type ChatMessageProps = {
  role?: string;
  content?: unknown;
  e: EntryDetail;
  flaggedAlert: DlpAlert | null;
  onOpenAlert: (id: string) => void;
  /** Show the per-message audit-layer strip (seq / entry_id / timestamp).
   *  Only truthful on tabs where every message was introduced by the
   *  current entry — i.e. "This turn". Suppressed on "Full context"
   *  where most messages came from prior entries. */
  showMeta?: boolean;
  /** Mark this message as introduced by the current entry. Adds a
   *  neutral primary-color outline so the user can locate "where I am"
   *  inside the full context. */
  current?: boolean;
  /** Forwarded ref-callback used by FullContextTab to scroll the first
   *  current message into view on mount. Accepts whatever element type
   *  the message ends up rendering as (button when flagged, div otherwise). */
  anchorRef?: (el: HTMLElement | null) => void;
};

function ChatMessage({
  role,
  content,
  e,
  flaggedAlert,
  onOpenAlert,
  showMeta = false,
  current = false,
  anchorRef,
}: ChatMessageProps) {
  const body = (
    <>
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-[11px] font-mono font-semibold uppercase tracking-wide text-primary">
          {role || "?"}
        </span>
        {showMeta && (
          <span className="text-[10px] font-mono text-muted-foreground/80 truncate">
            seq #{e.seq} · {e.entry_id.slice(0, 8)}… · {e.dt}
          </span>
        )}
        {current && !showMeta && (
          <span className="text-[10px] font-mono uppercase tracking-wider text-primary">
            this turn
          </span>
        )}
        {flaggedAlert && (
          <span className="ml-auto inline-flex items-center gap-1 rounded border border-sev-critical/20 bg-sev-critical/10 px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase tracking-wider text-sev-critical">
            <ShieldAlert className="h-3 w-3" />
            DLP — click for details
          </span>
        )}
      </div>
      <div className="text-[13px] text-muted-foreground whitespace-pre-wrap break-words leading-relaxed text-left">
        {String(content ?? "")}
      </div>
    </>
  );

  // Visual layers (lowest priority first):
  //   - default       — neutral card
  //   - current       — primary outline ("you are here" on Full context)
  //   - flaggedAlert  — red, clickable; alert wins over current
  if (flaggedAlert) {
    return (
      <button
        ref={anchorRef}
        type="button"
        className="w-full rounded-md border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 mb-2 hover:border-sev-critical/50 hover:bg-sev-critical/10 transition-colors"
        onClick={() => onOpenAlert(flaggedAlert.alert_id ?? String(flaggedAlert.id))}
        title={`Open alert ${formatAlertId(flaggedAlert.serial_id ?? flaggedAlert.id)}`}
      >
        {body}
      </button>
    );
  }
  return (
    <div
      ref={anchorRef}
      className={cn(
        "rounded-md px-4 py-3 mb-2 border",
        current
          ? "border-primary/60 bg-primary/5"
          : "border-border bg-card",
      )}
    >
      {body}
    </div>
  );
}

function MetadataTab({
  e,
  onOpenAlert,
}: {
  e: EntryDetail;
  onOpenAlert: (id: string) => void;
}) {
  const alerts = e.dlp_alerts ?? [];
  // Tri-state, not a boolean: an entry with NO signature is "unsigned"
  // (starter edition — hash-chained but not cryptographically signed), which
  // is a benign, expected state — not a verification failure. Only show the
  // red "invalid" when a signature is actually present and fails to verify.
  const signed = !!e.signature;
  return (
    <div>
      <div className="mb-4">
        {signed ? (
          <span
            className={cn(
              "text-[13px] font-semibold",
              e.signature_valid ? "text-success" : "text-destructive",
            )}
          >
            {e.signature_valid ? "✓ Signature valid" : "✗ Signature invalid"}
          </span>
        ) : (
          <span
            className="text-[13px] font-medium text-muted-foreground"
            title="Hash-chained and tamper-evident, but not cryptographically signed. Independent audit signing is available in the KYDE Enterprise edition."
          >
            Unsigned · hash-chain verified
          </span>
        )}
      </div>
      {alerts.length > 0 && (
        <section className="mb-6 rounded-md border border-sev-critical/20 bg-sev-critical/5 p-3">
          <p className="mb-2 text-[11px] font-mono font-semibold uppercase tracking-wide text-sev-critical">
            DLP alerts on this entry ({alerts.length})
          </p>
          <ul className="space-y-1">
            {alerts.map((a) => {
              const sev = getSeverity(a);
              return (
                <li key={a.alert_id ?? a.id}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-3 rounded border border-transparent px-2 py-1.5 text-left text-sm hover:border-sev-critical/20 hover:bg-sev-critical/10"
                    onClick={() => onOpenAlert(a.alert_id ?? String(a.id))}
                    title="Open alert details"
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-sev-critical" />
                      <span className="font-mono text-xs">
                        {formatAlertId(a.serial_id ?? a.id)}
                      </span>
                      <span className="truncate text-muted-foreground">
                        {getAlertType(a)}
                      </span>
                    </span>
                    <span
                      className={cn(
                        "inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase",
                        SEV_STYLE[sev],
                      )}
                    >
                      {sev}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
        <Field label="Seq"       value={<span className="font-mono">{e.seq}</span>} />
        <Field label="Action"    value={<ActionBadge type={e.action_type} />} />
        <Field label="Timestamp" value={<span className="font-mono">{e.dt}</span>} copyable={e.dt} />
        <Field label="Model"     value={<span className="font-mono">{e.model}</span>} copyable={e.model} />
        <Field
          label="Agent"
          value={
            <NavLink
              to={`/agents/${encodeURIComponent(e.agent_id)}`}
              className="font-mono text-primary hover:underline"
              title={`Open agent ${e.agent_id}`}
            >
              {e.agent_id}
            </NavLink>
          }
          copyable={e.agent_id}
        />
        <Field label="Upstream"  value={e.upstream} />
        <Field
          label="Session"
          value={
            e.session_id ? (
              <NavLink
                to={`/sessions/${e.session_id}`}
                className="font-mono text-primary hover:underline"
                title={`Open session ${e.session_id}`}
              >
                {e.session_id}
              </NavLink>
            ) : (
              <span className="font-mono">-</span>
            )
          }
          copyable={e.session_id ?? undefined}
        />
        <Field
          label="Host"
          value={
            e.client_ip ? (
              <NavLink
                to={`/hosts/${encodeURIComponent(e.client_ip)}`}
                className="block text-primary hover:underline"
                title={`Open host ${e.client_hostname ?? e.client_ip}`}
              >
                {e.client_hostname ? (
                  <>
                    <span className="break-all">{e.client_hostname}</span>
                    <span className="block font-mono text-[11px] text-muted-foreground">
                      {e.client_ip}
                    </span>
                  </>
                ) : (
                  <span className="font-mono">{e.client_ip}</span>
                )}
              </NavLink>
            ) : (
              <span className="font-mono">-</span>
            )
          }
          copyable={e.client_ip ?? undefined}
        />
        <Field label="Prompt tokens"     value={e.prompt_tokens ?? "-"} />
        <Field label="Completion tokens" value={e.completion_tokens ?? "-"} />
        <Field label="User-agent" value={e.user_agent ?? "-"} />
        <Field label="Entry ID"   value={<span className="font-mono">{e.entry_id}</span>} copyable={e.entry_id} />
      </div>
    </div>
  );
}

function RedactedNotice() {
  return (
    <p className="italic text-sm text-muted-foreground">
      Message content is restricted to the auditor role.
    </p>
  );
}

function ThisTurnTab({
  e,
  onOpenAlert,
}: {
  e: EntryDetail;
  onOpenAlert: (id: string) => void;
}) {
  if (e.content_redacted) return <RedactedNotice />;
  const full = e.full_messages_parsed ?? [];
  const why = e.why_parsed ?? [];
  const offset = e.new_message_offset ?? 0;
  const delta = full.slice(offset);
  const alerts = e.dlp_alerts ?? [];

  return (
    <div>
      <div className="mb-4 rounded border border-border bg-muted/30 px-3 py-2 text-[11px] font-mono text-muted-foreground">
        <span className="font-semibold">audit layer:</span> seq #{e.seq} ·{" "}
        <span className="break-all">{e.entry_id}</span> ·{" "}
        <span>{e.dt}</span>
      </div>

      <section className="mb-6">
        <h3 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          {offset === 0
            ? `New conversation (${delta.length} message${delta.length === 1 ? "" : "s"})`
            : `Appended this turn (${delta.length} message${delta.length === 1 ? "" : "s"})`}
        </h3>
        {delta.length ? (
          delta.map((m, i) => (
            <ChatMessage
              key={i}
              role={m.role}
              content={String(m.content ?? "").slice(0, 2000)}
              e={e}
              flaggedAlert={findFlaggingAlert(m.content, alerts)}
              onOpenAlert={onOpenAlert}
              showMeta
            />
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            No new messages on this entry — `full_messages` matches the prior entry.
          </p>
        )}
      </section>

      <section className="mb-6">
        <h3 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Assistant response
        </h3>
        {e.response_body_parsed == null ? (
          <p className="text-[11px] font-mono text-muted-foreground/80">
            Recorded before response bodies were retained — preserved as{" "}
            <code>output_hash</code> only (see Hashes tab).
          </p>
        ) : e.assistant_text ? (
          <ChatMessage
            role="assistant"
            content={e.assistant_text}
            e={e}
            flaggedAlert={findFlaggingAlert(e.assistant_text, alerts)}
            onOpenAlert={onOpenAlert}
            showMeta
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            No assistant text on this response
            {(e.tool_calls_parsed?.length ?? 0) > 0
              ? " — it carried tool calls only (see Tools tab)."
              : "."}
          </p>
        )}
      </section>

      {why.length > 0 && (
        <section>
          <h3 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Reasoning context (why)
          </h3>
          {why.map((m, i) => (
            <ChatMessage
              key={i}
              role={m.role}
              content={m.content}
              e={e}
              flaggedAlert={findFlaggingAlert(m.content, alerts)}
              onOpenAlert={onOpenAlert}
              showMeta
            />
          ))}
        </section>
      )}
    </div>
  );
}

function FullContextTab({
  e,
  onOpenAlert,
}: {
  e: EntryDetail;
  onOpenAlert: (id: string) => void;
}) {
  if (e.content_redacted) return <RedactedNotice />;
  const full = e.full_messages_parsed ?? [];
  const offset = e.new_message_offset ?? 0;
  const alerts = e.dlp_alerts ?? [];

  // Scroll the first "current" message into view so the user lands at
  // the bottom of the conversation when they switch to this tab. Falls
  // through cleanly when there are no new messages (offset == length).
  const anchorRef = (el: HTMLElement | null) => {
    if (el) el.scrollIntoView({ block: "start", behavior: "auto" });
  };

  if (full.length === 0) {
    return <p className="text-sm text-muted-foreground">No message history captured.</p>;
  }

  return (
    <div>
      <p className="mb-3 text-[11px] font-mono text-muted-foreground/80">
        Showing the complete <code>full_messages</code> array sent to the LLM on this call
        ({full.length} message{full.length === 1 ? "" : "s"}).
        Messages outlined in blue are the ones this entry contributed.
      </p>
      {full.map((m, i) => {
        const isCurrent = i >= offset;
        return (
          <ChatMessage
            key={i}
            role={m.role}
            content={String(m.content ?? "").slice(0, 2000)}
            e={e}
            flaggedAlert={findFlaggingAlert(m.content, alerts)}
            onOpenAlert={onOpenAlert}
            current={isCurrent}
            anchorRef={isCurrent && i === offset ? anchorRef : undefined}
          />
        );
      })}
    </div>
  );
}

function ToolsTab({ e }: { e: EntryDetail }) {
  const tcs = e.tool_calls_parsed ?? [];
  if (!tcs.length) return <p className="text-sm text-muted-foreground">No tool calls.</p>;
  return (
    <div>
      {tcs.map((tc, i) => (
        <div key={i} className="mb-4">
          <div className="mb-1 text-[13px] font-semibold">
            {i + 1}. <span className="font-mono text-warning">{tc.function ?? "?"}</span>
          </div>
          <CodeBlock>{JSON.stringify(tc.args ?? {}, null, 2)}</CodeBlock>
        </div>
      ))}
    </div>
  );
}

function HashesTab({ e }: { e: EntryDetail }) {
  return (
    <div className="grid grid-cols-1 gap-2">
      <Field label="Input hash"  value={<span className="font-mono text-xs">{e.input_hash ?? "-"}</span>}  copyable={e.input_hash ?? undefined} />
      <Field label="Output hash" value={<span className="font-mono text-xs">{e.output_hash ?? "-"}</span>} copyable={e.output_hash ?? undefined} />
      <Field label="Prev hash"   value={<span className="font-mono text-xs">{e.prev_hash ?? "-"}</span>}   copyable={e.prev_hash ?? undefined} />
      <Field label="Entry hash"  value={<span className="font-mono text-xs">{e.entry_hash ?? "-"}</span>}  copyable={e.entry_hash ?? undefined} />
      <Field label="Signature"   value={<span className="font-mono text-xs text-muted-foreground">{e.signature ? e.signature : "— (unsigned)"}</span>} copyable={e.signature || undefined} />
    </div>
  );
}

export function EntryDetailDialog() {
  const { ref, open: openRef, close } = useEntryRef();
  const { data, isLoading, isError, error } = useEntry(ref);
  const { isAuditor } = useMe();
  // Selected alert id within this entry — clicking an alert row on the
  // Metadata tab or a red-flagged message on the Messages tab opens the
  // DlpAlertDetail sheet on top of the entry dialog.
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);

  const selectedAlert = useMemo<DlpAlert | null>(() => {
    if (!selectedAlertId || !data?.dlp_alerts) return null;
    return (
      data.dlp_alerts.find(
        (a) => (a.alert_id ?? String(a.id)) === selectedAlertId,
      ) ?? null
    );
  }, [selectedAlertId, data]);

  const seq = ref ? Number(ref) : null;
  const canGoPrev = seq !== null && !Number.isNaN(seq) && seq > 1;
  const canGoNext = seq !== null && !Number.isNaN(seq);

  return (
    <Dialog open={!!ref} onOpenChange={(open) => !open && close()}>
      {/* Fixed-size window: header + tabs stay pinned, only the body scrolls.
          h-[85vh] keeps every tab the same size regardless of content length. */}
      <DialogContent className="flex h-[85vh] max-w-5xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 space-y-0 border-b border-border px-6 py-4">
          <div className="flex items-center justify-between gap-4">
            <DialogTitle>
              Entry Detail{data ? <span className="ml-2 font-mono text-sm text-muted-foreground">#{data.seq}</span> : null}
            </DialogTitle>
            <div className="mr-8 flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => {
                  navigator.clipboard.writeText(window.location.href);
                  toast.success("Link copied");
                }}
                aria-label="Copy shareable link"
              >
                <LinkIcon className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                disabled={!canGoPrev}
                onClick={() => seq && openRef(String(seq - 1))}
                aria-label="Previous entry"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                disabled={!canGoNext}
                onClick={() => seq !== null && openRef(String(seq + 1))}
                aria-label="Next entry"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </DialogHeader>

        {isLoading ? (
          <div className="flex-1 space-y-3 overflow-y-auto px-6 py-4">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : isError ? (
          <div className="flex-1 overflow-y-auto px-6 py-4">
            <p className="text-sm text-destructive">
              Failed to load entry: {(error as Error)?.message ?? "unknown error"}
            </p>
          </div>
        ) : data ? (
          <Tabs defaultValue="metadata" className="flex min-h-0 flex-1 flex-col">
            <TabsList className="mx-6 mt-4 shrink-0 self-start">
              <TabsTrigger value="metadata">Metadata</TabsTrigger>
              <TabsTrigger value="this-turn">This turn</TabsTrigger>
              <TabsTrigger value="full-context">Full context</TabsTrigger>
              <TabsTrigger value="tools">Tools</TabsTrigger>
              <TabsTrigger value="hashes">Hashes</TabsTrigger>
            </TabsList>
            {/* The single scroll region — content swaps inside it, so the
                header and tab bar above never move. */}
            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
              <TabsContent value="metadata" className="mt-0">
                <MetadataTab e={data} onOpenAlert={setSelectedAlertId} />
              </TabsContent>
              <TabsContent value="this-turn" className="mt-0">
                <ThisTurnTab e={data} onOpenAlert={setSelectedAlertId} />
              </TabsContent>
              <TabsContent value="full-context" className="mt-0">
                <FullContextTab e={data} onOpenAlert={setSelectedAlertId} />
              </TabsContent>
              <TabsContent value="tools" className="mt-0"><ToolsTab e={data} /></TabsContent>
              <TabsContent value="hashes" className="mt-0"><HashesTab e={data} /></TabsContent>
            </div>
          </Tabs>
        ) : null}
      </DialogContent>

      <Sheet
        open={!!selectedAlert}
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
    </Dialog>
  );
}
