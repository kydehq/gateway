import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Trash2, Plus, Loader2 } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useMcpPolicies,
  useSetMcpPolicy,
  useDeleteMcpPolicy,
} from "@/api/queries";
import type { McpServer, McpToolPolicy } from "@/api/types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: McpServer | null;
  /** Auditors may view policies but not change them. */
  readOnly?: boolean;
}

// One row in the policy matrix. The precedence ladder (most-specific-wins)
// is enforced server-side by mcp_proxy.check_policy — this component is
// just CRUD over the table that feeds it.
export function McpPolicySheet({ open, onOpenChange, server, readOnly = false }: Props) {
  const serverName = server?.name ?? "";
  const { data, isLoading, isError, error } = useMcpPolicies(
    serverName,
    open && !!serverName,
  );
  const setPolicy = useSetMcpPolicy(serverName);
  const deletePolicy = useDeleteMcpPolicy(serverName);

  // New-row inputs. We keep them small and free-text — no constraint on
  // agent_id/tool_name lets operators write "*" wildcards directly.
  const [newAgent, setNewAgent] = useState("*");
  const [newTool, setNewTool] = useState("");
  const [newReason, setNewReason] = useState("");

  const sorted = useMemo(() => {
    if (!data) return [];
    // Wildcards first (broadest scope), then sorted by (agent_id, tool_name)
    // so the human-readable "lockdown" row sits at the top.
    return [...data].sort((a, b) => {
      const aw = a.agent_id === "*" ? 0 : 1;
      const bw = b.agent_id === "*" ? 0 : 1;
      if (aw !== bw) return aw - bw;
      const cmp = a.agent_id.localeCompare(b.agent_id);
      if (cmp !== 0) return cmp;
      return a.tool_name.localeCompare(b.tool_name);
    });
  }, [data]);

  const addRow = async (decision: "allow" | "deny") => {
    const agent_id = newAgent.trim() || "*";
    const tool_name = newTool.trim();
    if (!tool_name) {
      toast.error("Tool name is required (use * for any tool)");
      return;
    }
    try {
      await setPolicy.mutateAsync({
        agent_id,
        tool_name,
        decision,
        reason: newReason.trim() || null,
      });
      toast.success(`${decision === "deny" ? "Deny" : "Allow"} rule added`);
      setNewTool("");
      setNewReason("");
    } catch (err) {
      toast.error((err as Error).message || "Failed to set policy");
    }
  };

  const toggle = async (row: McpToolPolicy) => {
    const next = row.decision === "deny" ? "allow" : "deny";
    try {
      await setPolicy.mutateAsync({
        agent_id: row.agent_id,
        tool_name: row.tool_name,
        decision: next,
        reason: row.reason,
      });
    } catch (err) {
      toast.error((err as Error).message || "Failed to toggle");
    }
  };

  const remove = async (row: McpToolPolicy) => {
    try {
      await deletePolicy.mutateAsync({
        agent_id: row.agent_id,
        tool_name: row.tool_name,
      });
    } catch (err) {
      toast.error((err as Error).message || "Failed to delete");
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[560px] sm:max-w-[560px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Per-tool policies — {serverName}</SheetTitle>
          <SheetDescription>
            Allow/deny rules for tools/call against this server.{" "}
            <code>*</code> is a literal wildcard. Precedence (most-specific
            wins): (agent, tool) ▸ (*, tool) ▸ (agent, *) ▸ (*, *). No row ⇒
            default allow.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* New rule form — admins only. */}
          {!readOnly && (
          <div className="rounded-md border border-border bg-card/40 p-3">
            <div className="mb-2 text-xs font-mono uppercase text-muted-foreground">
              Add rule
            </div>
            <div className="grid grid-cols-[1fr_1fr] gap-2">
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">
                  Agent ID
                </label>
                <Input
                  value={newAgent}
                  onChange={(e) => setNewAgent(e.target.value)}
                  placeholder="* or agent:abc123"
                  className="h-8 text-xs"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">
                  Tool name
                </label>
                <Input
                  value={newTool}
                  onChange={(e) => setNewTool(e.target.value)}
                  placeholder="search or *"
                  className="h-8 text-xs"
                />
              </div>
            </div>
            <div className="mt-2">
              <label className="mb-1 block text-[11px] text-muted-foreground">
                Reason (optional, shown in JSON-RPC error message on deny)
              </label>
              <Input
                value={newReason}
                onChange={(e) => setNewReason(e.target.value)}
                placeholder="e.g. handles PII"
                className="h-8 text-xs"
              />
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => addRow("allow")}
                disabled={setPolicy.isPending}
              >
                <Plus className="mr-1 h-3 w-3" /> Allow
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => addRow("deny")}
                disabled={setPolicy.isPending}
              >
                <Plus className="mr-1 h-3 w-3" /> Deny
              </Button>
            </div>
          </div>
          )}

          {/* Existing rules */}
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead>Tool</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead className="w-12" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 2 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell colSpan={5}>
                        <Skeleton className="h-4 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : isError ? (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="py-6 text-center text-sm text-destructive"
                    >
                      Failed to load: {(error as Error)?.message ?? "unknown"}
                    </TableCell>
                  </TableRow>
                ) : sorted.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="py-6 text-center text-xs text-muted-foreground"
                    >
                      No rules — every tools/call is allowed (default).
                    </TableCell>
                  </TableRow>
                ) : (
                  sorted.map((row) => (
                    <TableRow key={`${row.agent_id}::${row.tool_name}`}>
                      <TableCell className="font-mono text-xs">
                        {row.agent_id}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {row.tool_name}
                      </TableCell>
                      <TableCell>
                        <button
                          type="button"
                          onClick={() => toggle(row)}
                          disabled={readOnly || setPolicy.isPending}
                          className={
                            row.decision === "deny"
                              ? "rounded-sm bg-destructive/15 px-1.5 py-0.5 text-[10px] font-mono uppercase text-destructive hover:bg-destructive/25"
                              : "rounded-sm bg-success/10 px-1.5 py-0.5 text-[10px] font-mono uppercase text-success hover:bg-success/20"
                          }
                          aria-label={`Toggle to ${row.decision === "deny" ? "allow" : "deny"}`}
                        >
                          {row.decision}
                        </button>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {row.reason || "—"}
                      </TableCell>
                      <TableCell>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => remove(row)}
                          disabled={readOnly || deletePolicy.isPending}
                          aria-label="Delete rule"
                        >
                          {deletePolicy.isPending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
