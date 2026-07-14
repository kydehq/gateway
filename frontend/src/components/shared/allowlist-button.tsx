import { useState } from "react";
import { ShieldOff } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateDlpRule } from "@/api/queries";

interface Props {
  scanner: string;
  entityType: string;
  matchText?: string | null;
  compact?: boolean;
}

// "Add to allowlist" control. Opens a small dialog pre-filled with the
// scanner + entity type + (optionally) the exact matched text, so the
// admin only has to choose scope and add a note.
//
// Two scopes:
//   exact : rule matches ONLY this exact text (normalized)
//   type  : rule matches every future finding of this entity_type
export function AllowlistButton({ scanner, entityType, matchText, compact }: Props) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<"exact" | "type">(
    matchText ? "exact" : "type",
  );
  const [note, setNote] = useState("");
  const createRule = useCreateDlpRule();

  async function onSubmit() {
    try {
      await createRule.mutateAsync({
        kind: "allow",
        scanner,
        entity_type: entityType,
        match_text: scope === "exact" ? matchText || null : null,
        note: note.trim(),
      });
      toast.success(
        scope === "exact"
          ? `Allowlisted ${entityType}: ${matchText}`
          : `Allowlisted every ${entityType} match`,
      );
      setOpen(false);
      setNote("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to add rule.";
      toast.error(msg);
    }
  }

  const label = "Allowlist";
  const triggerClass = compact
    ? "h-6 gap-1 px-2 text-[11px]"
    : "h-7 gap-1.5 px-2.5 text-xs";

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={triggerClass}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        <ShieldOff className="h-3 w-3" />
        {label}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Add allowlist rule</DialogTitle>
            <DialogDescription>
              Matching findings will be dropped before they become alerts.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 rounded-md border border-border bg-muted/30 p-3 text-xs">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  Scanner
                </div>
                <div className="font-mono">{scanner}</div>
              </div>
              <div>
                <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  Entity type
                </div>
                <div className="break-all font-mono">{entityType}</div>
              </div>
              {matchText ? (
                <div className="col-span-2">
                  <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                    Matched text
                  </div>
                  <div className="break-all font-mono">{matchText}</div>
                </div>
              ) : null}
            </div>

            <div>
              <Label htmlFor="allow-scope">Scope</Label>
              <Select
                value={scope}
                onValueChange={(v) => setScope(v as "exact" | "type")}
              >
                <SelectTrigger id="allow-scope">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {matchText ? (
                    <SelectItem value="exact">
                      Only this exact match
                    </SelectItem>
                  ) : null}
                  <SelectItem value="type">
                    Every {entityType} match (broad)
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">
                {scope === "exact"
                  ? "Future occurrences of this exact value won't raise alerts."
                  : `Every future ${entityType} finding from ${scanner} will be suppressed.`}
              </p>
            </div>

            <div>
              <Label htmlFor="allow-note">Note (optional)</Label>
              <Input
                id="allow-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. benign test data, internal system email"
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={createRule.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={onSubmit}
              disabled={createRule.isPending || !entityType}
            >
              {createRule.isPending ? "Adding…" : "Add rule"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
