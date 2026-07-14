import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props {
  label: string;
  value: ReactNode;
  /** Smaller, monospace treatment for timestamps / IDs. */
  small?: boolean;
  /** Semantic tint. Per Editorial Mono (spec §5, Principle 5) numeric KPIs
   *  stay neutral-black regardless of tone — only a textual *status word*
   *  (e.g. "VERIFIED" / "BROKEN") carries the tone color (spec §6.3). */
  tone?: "default" | "success" | "destructive" | "info" | "warning";
  /** Optional second line under the value (e.g. "@ €0.92 / $1"). */
  subtext?: ReactNode;
  /** When set, the card becomes a router link to this path. */
  to?: string;
}

const TONE: Record<NonNullable<Props["tone"]>, string> = {
  default: "text-foreground",
  success: "text-success",
  destructive: "text-destructive",
  info: "text-info",
  warning: "text-warning",
};

export function MetricCard({ label, value, small, tone = "default", subtext, to }: Props) {
  // A string value with a non-default tone is treated as a status *word*
  // ("VERIFIED"), which may carry color; everything else — counts, numbers —
  // renders neutral-black so severity never leaks onto a KPI number.
  const isStatusWord = tone !== "default" && typeof value === "string";

  const card = (
    <Card className={to ? "transition-colors hover:bg-accent/40 hover:border-accent" : undefined}>
      <CardContent className="p-5 flex flex-col gap-3.5">
        <div className="eyebrow">{label}</div>
        {small ? (
          <div className="font-mono text-sm font-medium text-foreground tabular-nums">{value}</div>
        ) : isStatusWord ? (
          <div className={cn("font-mono text-[26px] font-semibold tracking-[-0.01em] tabular-nums leading-none", TONE[tone])}>
            {value}
          </div>
        ) : (
          <div className="stat-value">{value}</div>
        )}
        {subtext ? (
          <div className="-mt-1 font-mono text-[11px] text-muted-foreground">{subtext}</div>
        ) : null}
      </CardContent>
    </Card>
  );

  if (to) {
    return (
      <Link to={to} className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-md">
        {card}
      </Link>
    );
  }
  return card;
}
