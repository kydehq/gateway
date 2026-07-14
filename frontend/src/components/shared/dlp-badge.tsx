import { StatusBadge, type StatusKind } from "@/components/shared/status-badge";

// Lookup is keyed by string (not the DlpStatus union) so historical
// rows carrying a legacy enum value (claimed, in_progress, pending_info,
// escalated) still render with a sensible fallback rather than a blank
// badge — useful when paging through an old audit trail.
// Status axis, NOT severity: new/closed are passive → neutral;
// in_review is live → active(blue); escalated is action-needed → bad(red).
const DLP_KIND: Record<string, StatusKind> = {
  new:       "neutral",
  in_review: "active",
  escalated: "bad",
  closed:    "neutral",
};

export function DlpBadge({ status }: { status: string }) {
  return <StatusBadge status={status} kind={DLP_KIND[status] ?? "neutral"} />;
}

// Dispositions are verdicts, not lifecycle states — but they live on the same
// quiet axis. Only a confirmed leak earns red; a false positive is benign, not
// "good", so it stays neutral (the "when in doubt, neutral" rule).
const DISPOSITION_KIND: Record<string, StatusKind> = {
  false_positive: "neutral",
  confirmed_leak: "bad",
  allowlisted:    "neutral",
  duplicate:      "neutral",
};

export function DispositionBadge({ disposition }: { disposition: string }) {
  return <StatusBadge status={disposition} kind={DISPOSITION_KIND[disposition] ?? "neutral"} />;
}
