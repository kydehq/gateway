import { cn } from "@/lib/utils";

// Status / State axis (DESIGN-2 §6.6) — a SEPARATE color system from severity.
// Lifecycle state is not a severity, so it never borrows the sev palette.
//   neutral — passive/default: OBSERVED, OPEN, NEW, CLOSED, ALLOWED, DIRECT
//   active  — running/live:    IN REVIEW, MONITORING, IN PROGRESS, CONNECTED
//   ok      — settled-good:    VERIFIED, RESOLVED, ENABLED, PASSED
//   bad     — blocked/action:  BLOCKED, ESCALATED, FAILED, DISABLED
export type StatusKind = "neutral" | "active" | "ok" | "bad";

const KIND_CLASS: Record<StatusKind, string> = {
  neutral: "",
  active: "badge-status-active",
  ok: "badge-status-ok",
  bad: "badge-status-bad",
};

// Map a raw status string (any casing / separators) to a kind. Default is
// neutral — color is opt-in, per the "when in doubt, neutral" rule.
const STATUS_KIND: Record<string, StatusKind> = {
  // neutral
  observed: "neutral", open: "neutral", new: "neutral", closed: "neutral",
  allowed: "neutral", direct: "neutral", pending: "neutral",
  // active
  in_review: "active", "in review": "active", monitoring: "active",
  in_progress: "active", "in progress": "active", connected: "active",
  // ok
  verified: "ok", resolved: "ok", enabled: "ok", passed: "ok", valid: "ok",
  // bad
  blocked: "bad", escalated: "bad", failed: "bad", disabled: "bad", broken: "bad",
};

export function statusKind(status: string): StatusKind {
  return STATUS_KIND[status.trim().toLowerCase()] ?? "neutral";
}

export function StatusBadge({
  status,
  kind,
  dot = false,
  className,
}: {
  /** Raw status string — rendered as label and (unless `kind` given) classified. */
  status: string;
  /** Force a kind, bypassing the classifier. */
  kind?: StatusKind;
  /** Show a leading dot in the badge's fg color. */
  dot?: boolean;
  className?: string;
}) {
  const k = kind ?? statusKind(status);
  return (
    <span className={cn("badge-status", KIND_CLASS[k], className)}>
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {status.replace(/_/g, " ")}
    </span>
  );
}
