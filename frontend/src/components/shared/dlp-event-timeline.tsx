import { useDlpAlertEvents } from "@/api/queries";
import type { DlpAlertEvent } from "@/api/types";

// Renders the per-alert triage audit trail (one row per status change /
// close). Drops a "No triage events yet" stub when there is nothing to
// show — that's the normal state for a freshly-detected alert.

export function DlpEventTimeline({ alertId }: { alertId: string | null }) {
  const { data: events, isLoading, isError } = useDlpAlertEvents(alertId);

  if (!alertId) return null;
  if (isLoading) {
    return <p className="text-xs text-muted-foreground">Loading events…</p>;
  }
  if (isError) {
    return <p className="text-xs text-destructive">Failed to load events.</p>;
  }
  return <EventList events={events ?? []} />;
}

function EventList({ events }: { events: DlpAlertEvent[] }) {
  if (events.length === 0) {
    return <p className="text-xs text-muted-foreground">No triage events yet.</p>;
  }
  return (
    <ol className="space-y-1.5 border-l border-border pl-3">
      {events.map((e) => (
        <li key={e.id} className="text-xs">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
              {formatTs(e.created_at)}
            </span>
            <span className="font-mono">
              {e.from_status ?? "—"} <span className="text-muted-foreground">→</span>{" "}
              <span className="font-semibold">{e.to_status ?? "—"}</span>
            </span>
            {e.disposition ? (
              <span className="font-mono text-muted-foreground">
                · {e.disposition.replace(/_/g, " ")}
              </span>
            ) : null}
            <span className="font-mono text-[10px] text-muted-foreground">
              [{e.actor_kind}]
            </span>
          </div>
          {e.note ? (
            <div className="mt-0.5 text-muted-foreground">{e.note}</div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function formatTs(ts: number): string {
  try {
    return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return String(ts);
  }
}
