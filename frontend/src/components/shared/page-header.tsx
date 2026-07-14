import type { ReactNode } from "react";
import { RelativeTime } from "./relative-time";

export function PageHeader({
  title,
  description,
  actions,
  lastUpdated,
}: {
  title: string;
  /** Plain string or a custom ReactNode (e.g. inline copy buttons + status chips). */
  description?: ReactNode;
  actions?: ReactNode;
  /** ms timestamp from TanStack Query's `dataUpdatedAt`, or any parseable date. */
  lastUpdated?: number | string | null;
}) {
  const updatedIso =
    typeof lastUpdated === "number"
      ? new Date(lastUpdated).toISOString()
      : typeof lastUpdated === "string"
      ? lastUpdated
      : null;

  return (
    <div className="mb-8 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-[28px] font-bold leading-[1.05] tracking-[-0.02em] text-foreground">{title}</h1>
        {description ? (
          <p className="text-[15px] text-muted-foreground mt-1.5">{description}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {updatedIso ? (
          <span className="hidden text-[11px] font-mono text-muted-foreground sm:inline">
            updated <RelativeTime value={updatedIso} />
          </span>
        ) : null}
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
