import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Copy, MoreHorizontal, Plus } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { ReadOnlyBadge } from "@/components/shared/read-only-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { McpServerDialog } from "@/components/shared/mcp-server-dialog";
import { McpPolicySheet } from "@/components/shared/mcp-policy-sheet";
import { useMe } from "@/hooks/use-me";
import { SortableTh, useSort } from "@/components/shared/sortable-th";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useDeleteMcpServer,
  useMcpAggregatorCatalog,
  useMcpServers,
} from "@/api/queries";
import type { McpServer } from "@/api/types";

// The gateway origin the operator should point their agents at. We use
// window.location.origin so the displayed URL matches however the
// operator reached the dashboard — single source of truth for the
// "where do my agents call" question, no env var to keep in sync.
function gatewayUrlFor(name: string): string {
  if (typeof window === "undefined") return `/mcp/${name}`;
  return `${window.location.origin}/mcp/${encodeURIComponent(name)}`;
}

function aggregatorUrl(): string {
  if (typeof window === "undefined") return "/mcp";
  return `${window.location.origin}/mcp`;
}

// "5m", "2h", "3d" — coarse age formatting for the banner so operators
// can eyeball staleness without scanning a full timestamp. Mirrors the
// shape used by RelativeTime but renders a compact chip-friendly string.
function formatAge(seconds: number | null): string {
  if (seconds == null) return "never seeded";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

type SortKey =
  | "name"
  | "upstream_url"
  | "enabled"
  | "created_at"
  | "last_call_at"
  | "last_error_at";

// 24h in ms — within this window we treat last_error_* as "currently
// flaky"; older errors are still surfaced but don't get the red chip.
const ERROR_RECENT_MS = 24 * 60 * 60 * 1000;

function isRecentError(ts: string | null): boolean {
  if (!ts) return false;
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t < ERROR_RECENT_MS;
}

export default function McpServersPage() {
  const { isAdmin } = useMe();
  const { data, isLoading, isError, error, dataUpdatedAt } = useMcpServers();
  const { data: catalog } = useMcpAggregatorCatalog();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | undefined>(undefined);
  const [toDelete, setToDelete] = useState<McpServer | null>(null);
  const [policiesFor, setPoliciesFor] = useState<McpServer | null>(null);
  const deleteM = useDeleteMcpServer();

  const { sort, toggle } = useSort<SortKey>({ key: "name", dir: "asc" });

  const sorted = useMemo(() => {
    if (!data) return [];
    const copy = [...data];
    copy.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (typeof av === "boolean" && typeof bv === "boolean") {
        const an = av ? 1 : 0;
        const bn = bv ? 1 : 0;
        return sort.dir === "asc" ? an - bn : bn - an;
      }
      const as = String(av ?? "").toLowerCase();
      const bs = String(bv ?? "").toLowerCase();
      return sort.dir === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
    });
    return copy;
  }, [data, sort]);

  const openAdd = () => {
    setEditing(undefined);
    setDialogOpen(true);
  };
  const openEdit = (s: McpServer) => {
    setEditing(s);
    setDialogOpen(true);
  };

  const copyUrl = (s: McpServer) => {
    navigator.clipboard.writeText(gatewayUrlFor(s.name));
    toast.success("Gateway URL copied");
  };

  const copyAggregatorUrl = () => {
    navigator.clipboard.writeText(aggregatorUrl());
    toast.success("Aggregator URL copied");
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    try {
      await deleteM.mutateAsync(toDelete.name);
      toast.success(`Removed ${toDelete.name}`);
    } catch (err) {
      toast.error((err as Error).message || "Delete failed");
    } finally {
      setToDelete(null);
    }
  };

  return (
    <>
      <PageHeader
        title="MCP Servers"
        description="Routing table for Model Context Protocol upstreams. Agents call /mcp/{name}; Kyde forwards their Authorization header unchanged."
        lastUpdated={dataUpdatedAt}
        actions={
          isAdmin ? (
            <Button size="sm" onClick={openAdd}>
              <Plus className="mr-1 h-4 w-4" /> Add MCP server
            </Button>
          ) : (
            <ReadOnlyBadge />
          )
        }
      />

      {/* Aggregator banner — one endpoint that fans out across every
          registered server via {server}__{tool} namespacing. Tools are
          seeded from real /tools/list traffic and the per-server probe
          button, so a stale catalog just means nobody's listed yet. */}
      <div className="mb-3 flex items-center justify-between gap-3 rounded-md border border-border bg-card px-4 py-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Aggregator endpoint
          </div>
          <div className="mt-0.5 break-all font-mono text-xs">
            {aggregatorUrl()}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            {catalog
              ? `${catalog.tool_count} tools across ${catalog.server_count} server${catalog.server_count === 1 ? "" : "s"} · oldest entry ${formatAge(catalog.oldest_seconds)}`
              : "Loading catalog…"}
          </div>
        </div>
        <Button
          size="icon"
          variant="ghost"
          onClick={copyAggregatorUrl}
          aria-label="Copy aggregator URL"
        >
          <Copy className="h-4 w-4" />
        </Button>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <SortableTh sortKey="name" sort={sort} toggle={toggle}>
                Name
              </SortableTh>
              <SortableTh sortKey="upstream_url" sort={sort} toggle={toggle}>
                Upstream URL
              </SortableTh>
              <SortableTh sortKey="enabled" sort={sort} toggle={toggle}>
                Status
              </SortableTh>
              <SortableTh sortKey="last_call_at" sort={sort} toggle={toggle}>
                Last call
              </SortableTh>
              <SortableTh sortKey="last_error_at" sort={sort} toggle={toggle}>
                Last error
              </SortableTh>
              <SortableTh sortKey="created_at" sort={sort} toggle={toggle}>
                Created
              </SortableTh>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="py-8 text-center text-sm text-destructive"
                >
                  Failed to load: {(error as Error)?.message ?? "unknown error"}
                </TableCell>
              </TableRow>
            ) : sorted.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  No MCP servers registered yet. Click "Add MCP server" to
                  register the first upstream.
                </TableCell>
              </TableRow>
            ) : (
              sorted.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-mono text-xs">
                    {s.name}
                    <div className="mt-0.5 text-[10px] text-muted-foreground">
                      {gatewayUrlFor(s.name)}
                    </div>
                  </TableCell>
                  <TableCell className="break-all text-xs">
                    {s.upstream_url}
                  </TableCell>
                  <TableCell>
                    <span
                      className={
                        s.enabled
                          ? "rounded-sm bg-success/10 px-1.5 py-0.5 text-[10px] font-mono uppercase text-success"
                          : "rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-mono uppercase text-muted-foreground"
                      }
                    >
                      {s.enabled ? "enabled" : "disabled"}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {s.last_call_at ? (
                      <RelativeTime value={s.last_call_at} />
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {s.last_error_at ? (
                      <span
                        className={
                          isRecentError(s.last_error_at)
                            ? "inline-flex items-center gap-1 rounded-sm bg-destructive/15 px-1.5 py-0.5 font-mono text-[10px] text-destructive"
                            : "inline-flex items-center gap-1 text-muted-foreground"
                        }
                        title={s.last_error_snippet ?? undefined}
                      >
                        {s.last_error_status != null ? (
                          <span className="font-semibold">
                            {s.last_error_status === 0
                              ? "ERR"
                              : s.last_error_status}
                          </span>
                        ) : null}
                        <RelativeTime value={s.last_error_at} />
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    <RelativeTime value={s.created_at} />
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => copyUrl(s)}
                        aria-label="Copy gateway URL"
                      >
                        <Copy className="h-4 w-4" />
                      </Button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            size="icon"
                            variant="ghost"
                            aria-label="MCP server actions"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem disabled={!isAdmin} onClick={() => openEdit(s)}>
                            Edit
                          </DropdownMenuItem>
                          {/* View-only: auditors may inspect per-tool policies. */}
                          <DropdownMenuItem onClick={() => setPoliciesFor(s)}>
                            Policies…
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            disabled={!isAdmin}
                            className="text-destructive focus:text-destructive"
                            onClick={() => setToDelete(s)}
                          >
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <McpServerDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        server={editing}
      />

      <McpPolicySheet
        open={!!policiesFor}
        onOpenChange={(o) => !o && setPoliciesFor(null)}
        server={policiesFor}
        readOnly={!isAdmin}
      />

      <AlertDialog
        open={!!toDelete}
        onOpenChange={(open) => !open && setToDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {toDelete?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Agents calling /mcp/{toDelete?.name} will receive a JSON-RPC
              error until you re-register the server. Per-tool policies for
              this server are deleted too.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
