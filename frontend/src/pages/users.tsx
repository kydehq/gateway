import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Copy, MoreHorizontal, Plus } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { UsersDialog } from "@/components/shared/users-dialog";
import { SortableTh, useSort } from "@/components/shared/sortable-th";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
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
  useDeleteUser,
  useResetUserPassword,
  useUnlockUser,
  useUsers,
} from "@/api/queries";
import { useMe } from "@/hooks/use-me";
import type { User } from "@/api/types";
import { cn } from "@/lib/utils";

function RoleChips({ roles }: { roles: string[] }) {
  if (!roles.length) return <span className="text-muted-foreground text-xs">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {roles.map((r) => (
        <span
          key={r}
          className={cn(
            "rounded-sm px-1.5 py-0.5 text-[10px] font-mono uppercase",
            r === "admin"
              ? "bg-destructive/15 text-destructive"
              : r === "auditor"
              ? "bg-info/15 text-info"
              : "bg-muted text-muted-foreground",
          )}
        >
          {r}
        </span>
      ))}
    </div>
  );
}

type SortKey = "username" | "email" | "status" | "created_at";

export default function UsersPage() {
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const { data, isLoading, isError, error } = useUsers(includeDeleted);
  const { me } = useMe();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<User | undefined>(undefined);

  const deleteM = useDeleteUser();
  const resetM = useResetUserPassword();
  const unlockM = useUnlockUser();
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<User | null>(null);

  const { sort, toggle } = useSort<SortKey>({ key: "username", dir: "asc" });

  const sorted = useMemo(() => {
    if (!data) return [];
    const copy = [...data];
    copy.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (typeof av === "number" && typeof bv === "number") {
        return sort.dir === "asc" ? av - bv : bv - av;
      }
      const as = String(av ?? "").toLowerCase();
      const bs = String(bv ?? "").toLowerCase();
      return sort.dir === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
    });
    return copy;
  }, [data, sort]);

  const openAdd = () => { setEditing(undefined); setDialogOpen(true); };
  const openEdit = (u: User) => { setEditing(u); setDialogOpen(true); };

  const onReset = async (u: User) => {
    try {
      const res = await resetM.mutateAsync(u.id);
      setTempPassword(res.temp_password);
      toast.success(`Password reset for ${u.username}`);
    } catch (err) {
      toast.error((err as Error).message || "Reset failed");
    }
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    try {
      await deleteM.mutateAsync(toDelete.id);
      toast.success(`Deleted ${toDelete.username}`);
    } catch (err) {
      toast.error((err as Error).message || "Delete failed");
    } finally {
      setToDelete(null);
    }
  };

  const onUnlock = async (u: User) => {
    try {
      await unlockM.mutateAsync(u.id);
      toast.success(`Unlocked ${u.username}`);
    } catch (err) {
      toast.error((err as Error).message || "Unlock failed");
    }
  };

  return (
    <>
      <PageHeader
        title="Users"
        description="Manage accounts, roles, and access"
        actions={<Button size="sm" onClick={openAdd}><Plus className="mr-1 h-4 w-4" /> Add user</Button>}
      />

      <div className="mb-3">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Checkbox
            checked={includeDeleted}
            onCheckedChange={(v) => setIncludeDeleted(v === true)}
          />
          Show deleted
        </label>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <SortableTh sortKey="username" sort={sort} toggle={toggle}>Username</SortableTh>
              <SortableTh sortKey="email" sort={sort} toggle={toggle}>Email</SortableTh>
              <TableHead>Roles</TableHead>
              <SortableTh sortKey="status" sort={sort} toggle={toggle}>Status</SortableTh>
              <SortableTh sortKey="created_at" sort={sort} toggle={toggle}>Created</SortableTh>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <TableRow key={i}><TableCell colSpan={6}><Skeleton className="h-4 w-full" /></TableCell></TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-sm text-destructive py-8">
                  Failed to load users: {(error as Error)?.message ?? "unknown error"}
                </TableCell>
              </TableRow>
            ) : sorted.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-8">
                  No users.
                </TableCell>
              </TableRow>
            ) : (
              sorted.map((u) => {
                const isSelf = me?.user_id != null && String(me.user_id) === String(u.id);
                return (
                  <TableRow key={String(u.id)} className={cn(u.deleted_at ? "opacity-50" : "")}>
                    <TableCell className="font-mono text-xs">
                      {u.username}
                      {isSelf ? (
                        <span className="ml-1.5 rounded-sm bg-muted px-1 py-px text-[9px] font-mono uppercase text-muted-foreground">
                          you
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-xs">{u.email ?? "-"}</TableCell>
                    <TableCell><RoleChips roles={u.roles} /></TableCell>
                    <TableCell className="text-xs">{u.status ?? (u.deleted_at ? "deleted" : "-")}</TableCell>
                    <TableCell className="text-xs"><RelativeTime value={u.created_at} /></TableCell>
                    <TableCell className="text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button size="icon" variant="ghost" aria-label="User actions">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEdit(u)}>Edit</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => onReset(u)}>Reset password</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => onUnlock(u)}>Unlock</DropdownMenuItem>
                          {/* Admins cannot delete themselves. The backend
                              also enforces this (400 cannot_delete_self),
                              so the toast will surface any edge case. */}
                          {!isSelf ? (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={() => setToDelete(u)}
                              >
                                Delete
                              </DropdownMenuItem>
                            </>
                          ) : null}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      <UsersDialog open={dialogOpen} onOpenChange={setDialogOpen} user={editing} />

      <Dialog open={!!tempPassword} onOpenChange={(open) => !open && setTempPassword(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Temporary password</DialogTitle>
          </DialogHeader>
          <p className="mb-3 text-sm text-muted-foreground">
            Share this once — it won't be shown again.
          </p>
          <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-3">
            <code className="flex-1 font-mono text-sm break-all">{tempPassword}</code>
            <Button
              size="icon"
              variant="ghost"
              onClick={() => {
                if (tempPassword) {
                  navigator.clipboard.writeText(tempPassword);
                  toast.success("Copied to clipboard");
                }
              }}
              aria-label="Copy"
            >
              <Copy className="h-4 w-4" />
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!toDelete} onOpenChange={(open) => !open && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {toDelete?.username}?</AlertDialogTitle>
            <AlertDialogDescription>
              This will mark the account as deleted. You can restore it later by toggling
              "Show deleted" and editing the record.
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
