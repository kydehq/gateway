import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateDlpRule,
  useDeleteDlpRule,
  useDlpRules,
} from "@/api/queries";
import type { DlpRule } from "@/api/types";

function scopeLabel(r: DlpRule): string {
  if (r.match_text) return "exact match";
  return "entity type";
}

function AddRuleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [scanner, setScanner] = useState<string>("regex");
  const [entityType, setEntityType] = useState("");
  const [matchText, setMatchText] = useState("");
  const [note, setNote] = useState("");
  const create = useCreateDlpRule();

  async function onSubmit() {
    if (!entityType.trim()) {
      toast.error("Entity type is required.");
      return;
    }
    try {
      await create.mutateAsync({
        kind: "allow",
        scanner: scanner === "any" ? null : scanner,
        entity_type: entityType.trim(),
        match_text: matchText.trim() || null,
        note: note.trim(),
      });
      toast.success("Rule added.");
      onOpenChange(false);
      setEntityType("");
      setMatchText("");
      setNote("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add rule.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Add allowlist rule</DialogTitle>
          <DialogDescription>
            Findings matching this rule will be dropped before they become alerts.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="new-scanner">Scanner</Label>
            <Select value={scanner} onValueChange={setScanner}>
              <SelectTrigger id="new-scanner">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="regex">regex</SelectItem>
                <SelectItem value="bert">bert</SelectItem>
                <SelectItem value="any">any (applies to both)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="new-entity">Entity type</Label>
            <Input
              id="new-entity"
              value={entityType}
              onChange={(e) => setEntityType(e.target.value)}
              placeholder="EMAIL_ADDRESS"
              autoComplete="off"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              The pattern name or classifier label (e.g. EMAIL_ADDRESS, AWS_ACCESS_KEY, PII).
            </p>
          </div>

          <div>
            <Label htmlFor="new-match">Match text (optional)</Label>
            <Input
              id="new-match"
              value={matchText}
              onChange={(e) => setMatchText(e.target.value)}
              placeholder="test@example.com"
              autoComplete="off"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Leave blank to allowlist every match of this entity type.
            </p>
          </div>

          <div>
            <Label htmlFor="new-note">Note (optional)</Label>
            <Input
              id="new-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Reason for the exception"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={create.isPending}>
            {create.isPending ? "Adding…" : "Add rule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteRuleDialog({
  rule,
  onClose,
}: {
  rule: DlpRule | null;
  onClose: () => void;
}) {
  const remove = useDeleteDlpRule();

  async function onConfirm() {
    if (!rule) return;
    try {
      await remove.mutateAsync(rule.id);
      toast.success("Rule removed.");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Remove failed.");
    }
  }

  return (
    <AlertDialog open={!!rule} onOpenChange={(v) => !v && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this rule?</AlertDialogTitle>
          <AlertDialogDescription>
            After removal, matching findings will again raise alerts. This
            cannot be undone — you would need to re-create the rule.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} disabled={remove.isPending}>
            {remove.isPending ? "Removing…" : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export default function DlpRulesPage() {
  const { data: rules, isLoading } = useDlpRules();
  const [addOpen, setAddOpen] = useState(false);
  const [toDelete, setToDelete] = useState<DlpRule | null>(null);

  return (
    <>
      <PageHeader
        title="DLP rules"
        description="Allowlist findings that are known to be benign so they no longer raise alerts or send emails."
      />

      <div className="mb-4 flex items-center justify-end">
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="mr-1.5 h-4 w-4" />
          Add rule
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-64" />
      ) : !rules || rules.length === 0 ? (
        <div className="rounded-md border border-dashed border-border bg-muted/20 p-8 text-center text-sm text-muted-foreground">
          No rules yet. Click <b>Add rule</b> above, or use the{" "}
          <i>Allowlist</i> button inside an alert's finding card.
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Kind</TableHead>
              <TableHead>Scanner</TableHead>
              <TableHead>Entity type</TableHead>
              <TableHead>Match</TableHead>
              <TableHead>Scope</TableHead>
              <TableHead>Note</TableHead>
              <TableHead className="text-right">Hits</TableHead>
              <TableHead>Last hit</TableHead>
              <TableHead>Added by</TableHead>
              <TableHead>Added</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.map((r) => (
              <TableRow key={r.id}>
                <TableCell>
                  <span
                    className={
                      r.kind === "allow"
                        ? "rounded-sm bg-success/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-success"
                        : "rounded-sm bg-destructive/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-destructive"
                    }
                  >
                    {r.kind}
                  </span>
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {r.scanner ?? (
                    <span className="text-muted-foreground">any</span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs font-semibold">
                  {r.entity_type}
                </TableCell>
                <TableCell className="max-w-[20ch] truncate font-mono text-xs">
                  {r.match_text ?? (
                    <span className="text-muted-foreground">(any)</span>
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {scopeLabel(r)}
                </TableCell>
                <TableCell className="max-w-[24ch] truncate text-xs text-muted-foreground">
                  {r.note || "—"}
                </TableCell>
                <TableCell className="text-right font-mono text-xs">
                  {r.hit_count.toLocaleString()}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {r.last_hit_at > 0 ? (
                    <RelativeTime value={r.last_hit_at} />
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {r.created_by_username ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <RelativeTime value={r.created_at} />
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    onClick={() => setToDelete(r)}
                    aria-label="Remove rule"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AddRuleDialog open={addOpen} onOpenChange={setAddOpen} />
      <DeleteRuleDialog rule={toDelete} onClose={() => setToDelete(null)} />
    </>
  );
}
