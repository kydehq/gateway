import { useMemo, useState } from "react";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebounced } from "@/hooks/use-debounced";
import { useAdminActions, type AdminAction } from "@/api/queries";

const ALL = "__all__";
const PAGE_SIZE = 100;

// Resource types that admin_actions can carry. Kept short — the action
// strings already encode the verb ("mcp_server.create" etc.), so this
// filter exists to slice "show me only DLP toggles" without typing.
const RESOURCE_TYPES: Array<{ value: string; label: string }> = [
  { value: "mcp_server", label: "MCP Server" },
  { value: "mcp_policy", label: "MCP Policy" },
  { value: "dlp_policy", label: "DLP Policy" },
];

function ActionPill({ action }: { action: string }) {
  // Split "mcp_server.create" → ("mcp_server", "create"). The verb gets
  // colored so destructive ops stand out at a glance.
  const verb = action.split(".").pop() ?? action;
  const tone =
    verb === "delete" || verb === "disable"
      ? "bg-destructive/15 text-destructive"
      : verb === "create" || verb === "enable"
        ? "bg-success/10 text-success"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={`inline-block rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase ${tone}`}
    >
      {action}
    </span>
  );
}

function diffSummary(row: AdminAction): string {
  // One-line description of what changed. before==null ⇒ create,
  // after==null ⇒ delete, both populated ⇒ update (diff keys).
  if (row.before == null && row.after != null) {
    return Object.keys(row.after).length
      ? `created (${Object.keys(row.after).length} fields)`
      : "created";
  }
  if (row.before != null && row.after == null) return "deleted";
  if (row.before != null && row.after != null) {
    const changed: string[] = [];
    for (const k of Object.keys(row.after)) {
      const a = JSON.stringify(row.after[k]);
      const b = JSON.stringify(row.before[k]);
      if (a !== b) changed.push(k);
    }
    return changed.length ? `changed ${changed.join(", ")}` : "no-op";
  }
  return "—";
}

export default function AdminActionsPage() {
  const [actionFilter, setActionFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState(ALL);
  const [page, setPage] = useState(0);
  const actionDebounced = useDebounced(actionFilter, 250);

  // Reset to first page whenever filters change so the offset doesn't
  // strand the user past the end of a now-smaller result set.
  const queryParams = useMemo(
    () => ({
      action: actionDebounced || null,
      resource_type: resourceFilter !== ALL ? resourceFilter : null,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [actionDebounced, resourceFilter, page],
  );

  const { data, isLoading, isError, error } = useAdminActions(queryParams);
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const onFilterChange = (fn: () => void) => {
    setPage(0);
    fn();
  };

  return (
    <>
      <PageHeader
        title="Admin Actions"
        description="Operational audit trail — every CRUD on MCP servers, MCP tool policies, and DLP policies. Separate from the signed ledger."
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          className="h-8 w-56 text-xs"
          placeholder="Filter by action (e.g. mcp_server.create)"
          value={actionFilter}
          onChange={(e) => onFilterChange(() => setActionFilter(e.target.value))}
        />
        <Select
          value={resourceFilter}
          onValueChange={(v) => onFilterChange(() => setResourceFilter(v))}
        >
          <SelectTrigger className="h-8 w-44 text-xs">
            <SelectValue placeholder="Resource type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All resources</SelectItem>
            {RESOURCE_TYPES.map((r) => (
              <SelectItem key={r.value} value={r.value}>
                {r.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-xs text-muted-foreground">
          {total.toLocaleString()} action{total === 1 ? "" : "s"}
        </span>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <TableHead className="w-44">When</TableHead>
              <TableHead className="w-36">Actor</TableHead>
              <TableHead className="w-56">Action</TableHead>
              <TableHead className="w-32">Resource</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={6}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-sm text-destructive"
                >
                  Failed to load: {(error as Error)?.message ?? "unknown error"}
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  No admin actions match.
                </TableCell>
              </TableRow>
            ) : (
              items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    <RelativeTime value={row.created_at} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {row.actor_username ?? (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <ActionPill action={row.action} />
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-muted-foreground">
                    {row.resource_type}
                  </TableCell>
                  <TableCell className="break-all font-mono text-[11px]">
                    {row.resource_id ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {diffSummary(row)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Server-side pagination — the audit log can grow without bound,
          so we never load the full set at once. */}
      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          Page {page + 1} of {totalPages}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </>
  );
}
