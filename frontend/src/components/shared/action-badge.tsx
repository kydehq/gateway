import { cn } from "@/lib/utils";

// Action-type badge. Per Editorial Mono, action *kinds* are not severities,
// so non-error kinds stay neutral; only true error/block states keep red.
const ACTION_STYLES: Record<string, string> = {
  chat:         "bg-primary/10 text-primary",
  tool_call:    "bg-secondary text-muted-foreground",
  tool_result:  "bg-secondary text-muted-foreground",
  error:        "bg-destructive/10 text-destructive",
  policy_block: "bg-destructive/10 text-destructive",
  // Phase B2: non-chat API endpoints that an operator flipped to
  // full_logging — embeddings, models-list, moderations, etc. The
  // specific endpoint kind lives on request_kind; action_type stays a
  // coarse bucket.
  api_call:     "bg-muted text-foreground",
};

export function ActionBadge({ type }: { type: string }) {
  const style = ACTION_STYLES[type] ?? "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-0.5 text-[11px] font-semibold font-mono tracking-wide",
        style,
      )}
    >
      {type}
    </span>
  );
}
