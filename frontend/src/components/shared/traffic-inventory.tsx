import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAgentTraffic, useSetTrafficMode } from "@/api/queries";
import type { AgentTrafficRow, PathKind, TrafficMode } from "@/api/types";
import { cn } from "@/lib/utils";

// Phase B1 inventory view: shows every (path_kind, count, last_seen, mode)
// row for one agent. Admin can flip mode between count_only and full_logging
// per row. The chat row's mode toggle is hidden — chat is always fully
// logged today, and there's no operator decision to make there.
//
// Phase B2 will wire the mode value into proxy behavior so flipping
// 'embedding' to 'full_logging' starts writing ledger rows for that
// agent's embeddings. In B1 the toggle is metadata-only.

// Human-readable labels for path_kind. Centralised here so the inventory
// table stays the single source of truth for the label-set.
const PATH_KIND_LABEL: Record<PathKind, string> = {
  chat: "Chat",
  embedding: "Embeddings",
  moderation: "Moderation",
  models_list: "Model listing",
  tokens_count: "Token count",
  audio_transcription: "Audio transcription",
  audio_translation: "Audio translation",
  audio_speech: "Audio synthesis",
  image_generation: "Image generation",
  image_edit: "Image edit",
  image_variation: "Image variation",
  legacy_completion: "Legacy completion",
  file_op: "File operations",
  fine_tuning: "Fine-tuning",
  unknown: "Unclassified",
};

function fmtRelTime(iso: string | null): string {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "—";
  const diffMs = Date.now() - ts;
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function ModeBadge({ mode }: { mode: TrafficMode }) {
  const isFull = mode === "full_logging";
  return (
    <span
      className={cn(
        "rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
        isFull
          ? "border-primary/30 bg-primary/10 text-primary"
          : "border-border bg-muted text-muted-foreground",
      )}
    >
      {isFull ? "full logging" : "count only"}
    </span>
  );
}

export function TrafficInventory({
  agentId,
  isAdmin,
}: {
  agentId: string;
  isAdmin: boolean;
}) {
  const { data: rows, isLoading } = useAgentTraffic(agentId);
  const setMode = useSetTrafficMode(agentId);

  if (isLoading) {
    return (
      <section className="rounded-md border bg-card p-5 mb-6">
        <h2 className="text-sm font-semibold mb-3">Traffic inventory</h2>
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      </section>
    );
  }

  const empty = !rows || rows.length === 0;

  return (
    <section className="rounded-md border bg-card p-5 mb-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Traffic inventory</h2>
        <p className="text-xs text-muted-foreground">
          Per-endpoint counters. Flip a non-chat row to{" "}
          <span className="font-mono">full logging</span> to capture full
          ledger rows for that traffic.
        </p>
      </div>

      {empty ? (
        <p className="rounded-md border border-dashed py-8 text-center text-sm text-muted-foreground">
          No traffic recorded yet for this agent.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-[11px] uppercase tracking-wider">
                Endpoint
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-right">
                Requests
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider">
                Last seen
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider">
                Mode
              </TableHead>
              {isAdmin && <TableHead className="w-32" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows!.map((row: AgentTrafficRow) => {
              const label =
                PATH_KIND_LABEL[row.path_kind] ?? PATH_KIND_LABEL.unknown;
              // chat is the always-logged baseline — no operator decision,
              // so hide the toggle to keep the page honest about what's
              // actionable.
              const showToggle = isAdmin && row.path_kind !== "chat";
              const nextMode: TrafficMode =
                row.mode === "full_logging" ? "count_only" : "full_logging";
              return (
                <TableRow key={row.path_kind}>
                  <TableCell className="text-sm">
                    <span>{label}</span>
                    <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                      {row.path_kind}
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">
                    {row.count.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {fmtRelTime(row.last_seen)}
                  </TableCell>
                  <TableCell>
                    <ModeBadge mode={row.mode} />
                  </TableCell>
                  {isAdmin && (
                    <TableCell className="text-right">
                      {showToggle ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={setMode.isPending}
                          onClick={() =>
                            setMode.mutate({
                              path_kind: row.path_kind,
                              mode: nextMode,
                            })
                          }
                        >
                          {nextMode === "full_logging"
                            ? "Enable logging"
                            : "Disable logging"}
                        </Button>
                      ) : null}
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
