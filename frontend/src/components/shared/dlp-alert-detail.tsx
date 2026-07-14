import { NavLink } from "react-router-dom";
import { SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { formatAlertId } from "@/lib/serial-ids";
import { useEntryRef } from "@/hooks/use-entry-ref";
import { useEntry } from "@/api/queries";
import type { DlpAlert } from "@/api/types";

// Mirrors _DLP_REDACTION_PLACEHOLDER in src/kyde/dashboard.py — the
// backend swaps matched_value / context_snippet for this string when the
// viewer doesn't hold the auditor role. We use it on the client to flag
// fields as "(redacted)" so it's obvious which values were obfuscated.
export const REDACTED_PLACEHOLDER = "<redacted — auditor role required>";

// Flat severity tints on the Editorial Mono palette (spec §6.5) — light tint
// surface + colored fg/border, one token family per severity.
export const SEV_STYLE: Record<string, string> = {
  CRITICAL: "bg-sev-critical/10 text-sev-critical border-sev-critical/20",
  HIGH:     "bg-sev-high/10 text-sev-high border-sev-high/20",
  MEDIUM:   "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
  LOW:      "bg-sev-low/10 text-sev-low border-sev-low/20",
};

export function getSeverity(alert: DlpAlert): "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" {
  if (alert.severity) return alert.severity.toUpperCase() as "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  if (alert.score >= 0.9) return "CRITICAL";
  if (alert.score >= 0.7) return "HIGH";
  if (alert.score >= 0.4) return "MEDIUM";
  return "LOW";
}

export function getAlertType(alert: DlpAlert): string {
  const s = alert.score;
  if (alert.scanner === "bert") return s > 0.85 ? "Data Exfiltration" : "PII Leak";
  if (alert.scanner === "regex") return s > 0.7 ? "Policy Violation" : "PII Leak";
  if (alert.scanner === "chain") return "Data Exfiltration";
  return "Anomaly";
}

type FindingShape = {
  source?: string;
  category?: string;
  severity?: string;
  confidence?: number;
  pattern_id?: string;
  pattern_name?: string;
  matched_value?: string;
  redacted_value?: string;
  context_snippet?: string;
  location?: [number, number];
  validator_passed?: boolean | null;
  validator_applied?: string | null;
  // BERT-style fallback fields seen on some alert payloads
  label?: string;
  action?: string;
  entity_type?: string;
  type?: string;
  text?: string;
  match?: string;
};

function parseFindings(
  raw: DlpAlert["findings_parsed"] | DlpAlert["findings"],
): FindingShape[] {
  if (!raw) return [];
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed as FindingShape[]) : [];
    } catch {
      return [];
    }
  }
  return Array.isArray(raw) ? (raw as unknown as FindingShape[]) : [];
}

function FindingCard({
  finding,
  scanner,
  isAuditor,
}: {
  finding: FindingShape;
  scanner: string;
  isAuditor: boolean;
}) {
  const title =
    finding.pattern_name ||
    finding.label ||
    finding.entity_type ||
    finding.type ||
    finding.category ||
    "Finding";

  const isBert =
    scanner === "bert" ||
    (!finding.pattern_id && !finding.matched_value && !finding.context_snippet);

  const matchValue = finding.matched_value ?? finding.match ?? finding.text;
  const redactedValue = finding.redacted_value;
  const contextSnippet = finding.context_snippet;
  const matchIsRedacted = matchValue === REDACTED_PLACEHOLDER;
  const contextIsRedacted = contextSnippet === REDACTED_PLACEHOLDER;

  const rows: Array<[string, React.ReactNode]> = [];
  if (finding.category)
    rows.push(["Category", <span className="font-mono text-xs">{finding.category}</span>]);
  if (finding.severity)
    rows.push(["Severity", <span className="font-mono text-xs">{finding.severity}</span>]);
  if (typeof finding.confidence === "number")
    rows.push([
      "Confidence",
      <span className="font-mono text-xs">{finding.confidence.toFixed(2)}</span>,
    ]);
  if (!isBert && finding.pattern_id)
    rows.push([
      "Pattern",
      <span className="font-mono text-xs break-all">{finding.pattern_id}</span>,
    ]);
  if (finding.location && Array.isArray(finding.location))
    rows.push([
      "Location",
      <span className="font-mono text-xs">
        [{finding.location[0]}, {finding.location[1]}]
      </span>,
    ]);
  if (finding.action)
    rows.push(["Action", <span className="font-mono text-xs">{finding.action}</span>]);
  if (typeof finding.validator_passed === "boolean")
    rows.push([
      "Validator",
      <span className="font-mono text-xs">
        {finding.validator_applied ?? "—"}: {finding.validator_passed ? "passed" : "failed"}
      </span>,
    ]);

  return (
    <div className="rounded border bg-card/50 p-3">
      <p className="text-sm font-semibold mb-2 break-all">{title}</p>
      <div className="space-y-1.5">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex items-start justify-between gap-4">
            <span className="text-xs text-muted-foreground shrink-0">{k}</span>
            <span className="text-right">{v}</span>
          </div>
        ))}
      </div>
      {!isBert && (matchValue || redactedValue) && (
        <div className="mt-3 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Match {matchIsRedacted && !isAuditor ? "(redacted)" : ""}
          </p>
          <pre className="rounded bg-muted px-2 py-1.5 font-mono text-[11px] whitespace-pre-wrap break-all">
            {isAuditor ? matchValue : (redactedValue ?? matchValue)}
          </pre>
        </div>
      )}
      {!isBert && contextSnippet && (
        <div className="mt-2 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Context {contextIsRedacted && !isAuditor ? "(redacted)" : ""}
          </p>
          <pre className="rounded bg-muted px-2 py-1.5 font-mono text-[11px] whitespace-pre-wrap break-all">
            {contextSnippet}
          </pre>
        </div>
      )}
    </div>
  );
}

export function FindingsSection({
  alert,
  isAuditor,
}: {
  alert: DlpAlert;
  isAuditor: boolean;
}) {
  const findings = parseFindings(alert.findings_parsed ?? alert.findings);
  if (findings.length === 0) return null;

  return (
    <section>
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        Findings
      </p>
      {alert.content_redacted && (
        <p className="mb-3 rounded border border-sev-medium/20 bg-sev-medium/10 px-3 py-2 text-xs text-sev-medium">
          Sensitive fields are obfuscated. Matched values and context require the auditor role.
        </p>
      )}
      <div className="space-y-3">
        {findings.map((f, i) => (
          <FindingCard
            key={i}
            finding={f}
            scanner={alert.scanner}
            isAuditor={isAuditor}
          />
        ))}
      </div>
    </section>
  );
}

// Auditor-only: pull the underlying ledger entry and render the captured
// messages inline so triage doesn't require a click-through to the
// entry-detail dialog. Content is the same `full_messages_parsed` the
// dialog uses, gated by the same role check at `dashboard.py:1043` — we
// just surface it next to the finding for context.
function CapturedMessagesSection({ entryId }: { entryId: string }) {
  const { data: entry, isLoading, error } = useEntry(entryId);

  // The auditor-redaction gate lives on the backend; if the API hands us
  // an empty array it's either a non-auditor session (shouldn't happen
  // because this section only mounts for auditors) or an entry with no
  // captured messages — show the same notice either way.
  const newOffset = entry?.new_message_offset ?? 0;
  const full = entry?.full_messages_parsed ?? [];
  // Default to "this turn" — the messages the current entry introduced.
  // Fall back to the whole context when the entry didn't add any (rare;
  // e.g. allow-listed retries that didn't push new messages).
  const turn = full.slice(newOffset);
  const messages = turn.length > 0 ? turn : full;

  return (
    <section>
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        Captured Messages
      </p>
      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}
      {error && (
        <p className="rounded border border-sev-medium/20 bg-sev-medium/10 px-3 py-2 text-xs text-sev-medium">
          Could not load captured messages: {(error as Error).message}
        </p>
      )}
      {entry && messages.length === 0 && (
        <p className="rounded border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          No captured messages for this entry.
        </p>
      )}
      {entry && messages.length > 0 && (
        <div className="space-y-2">
          {messages.map((m, i) => (
            <div key={i} className="rounded-md border border-border bg-card px-3 py-2">
              <p className="mb-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-primary">
                {m.role || "?"}
              </p>
              <pre className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-muted-foreground">
                {String(m.content ?? "")}
              </pre>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// Read-only header + What-Happened + Findings sections. Triage actions
// stay on the threats page where they own the local state (assignee,
// notes, bulk select, etc.); other surfaces (agent-chains, audit-log)
// embed this for inspection only.
export function DlpAlertDetail({
  alert,
  isAuditor,
  onEntityLinkClick,
}: {
  alert: DlpAlert;
  isAuditor: boolean;
  /** Called when the user clicks the agent / session link or the Entry
   *  button, so the host can close the sheet before the route changes
   *  (or before the global entry-detail dialog opens on top). */
  onEntityLinkClick?: () => void;
}) {
  const { open: openEntry } = useEntryRef();

  const rows: Array<[string, React.ReactNode]> = [
    [
      "Severity",
      <span className={cn("font-semibold", SEV_STYLE[getSeverity(alert)])}>
        {getSeverity(alert)}
      </span>,
    ],
    ["Type", getAlertType(alert)],
    ["Scanner", <span className="font-mono text-xs">{alert.scanner}</span>],
    ...(alert.prevented
      ? ([
          [
            "Prevented",
            <span className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-1.5 py-0.5 text-[10px] font-mono uppercase text-sev-critical">
              request blocked
            </span>,
          ],
        ] as Array<[string, React.ReactNode]>)
      : []),
    [
      "Score",
      <span className="font-mono text-xs">
        {alert.score.toFixed(2)} (0.4=MEDIUM · 0.7=HIGH · 0.9=CRITICAL)
      </span>,
    ],
    ["Detected", new Date(alert.created_dt).toLocaleString("de-DE")],
  ];
  if (alert.agent_id) {
    rows.push([
      "Agent",
      <NavLink
        to={`/agents/${encodeURIComponent(alert.agent_id)}`}
        className="font-mono text-xs text-primary hover:underline break-all"
        onClick={onEntityLinkClick}
        title={`Open agent ${alert.agent_id}`}
      >
        {alert.agent_id}
      </NavLink>,
    ]);
  }
  if (alert.session_id) {
    rows.push([
      "Session",
      <NavLink
        to={`/sessions/${alert.session_id}`}
        className="font-mono text-xs text-primary hover:underline break-all"
        onClick={onEntityLinkClick}
        title={`Open session ${alert.session_id}`}
      >
        {alert.session_id}
      </NavLink>,
    ]);
  }
  if (alert.entry_id) {
    rows.push([
      "Entry",
      <button
        type="button"
        className="font-mono text-xs text-primary hover:underline break-all text-right"
        onClick={() => {
          // Close the host sheet so the global entry-detail dialog
          // surfaces in front; the URL `?entry=` param drives the dialog.
          onEntityLinkClick?.();
          openEntry(alert.entry_id!);
        }}
        title={`Open entry ${alert.entry_id}`}
      >
        {alert.entry_id.length > 16 ? `${alert.entry_id.slice(0, 16)}…` : alert.entry_id}
      </button>,
    ]);
  }

  return (
    <>
      <SheetHeader className="mb-6">
        <SheetTitle className="font-mono">
          {formatAlertId(alert.serial_id ?? alert.id)} — {getAlertType(alert)}
          {alert.prevented && (
            <span className="ml-2 align-middle rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-1.5 py-0.5 text-[10px] font-mono uppercase text-sev-critical">
              prevented
            </span>
          )}
        </SheetTitle>
      </SheetHeader>
      <div className="space-y-6 text-sm">
        <section>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
            What Happened
          </p>
          <div className="space-y-2">
            {rows.map(([label, val], i) => (
              <div key={i} className="flex items-start justify-between gap-4">
                <span className="text-muted-foreground shrink-0">{label}</span>
                <span className="text-right">{val}</span>
              </div>
            ))}
          </div>
        </section>
        <FindingsSection alert={alert} isAuditor={isAuditor} />
        {isAuditor && alert.entry_id && (
          <CapturedMessagesSection entryId={alert.entry_id} />
        )}
      </div>
    </>
  );
}
