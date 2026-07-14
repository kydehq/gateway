import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RotateCcw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RelativeTime } from "./relative-time";
import { useResetSetting, useUpdateSetting } from "@/api/queries";
import type { SettingEntry } from "@/api/types";
import { cn } from "@/lib/utils";

const SOURCE_STYLE: Record<SettingEntry["source"], string> = {
  db:      "bg-info/15 text-info",
  env:     "bg-muted text-muted-foreground",
  default: "bg-muted text-muted-foreground",
};

export function SettingField({ entry }: { entry: SettingEntry }) {
  const [draft, setDraft] = useState(String(entry.value));
  // Reset the draft whenever the canonical value shifts (another admin
  // edits it, or we just refetched after our own PATCH).
  useEffect(() => { setDraft(String(entry.value)); }, [entry.value]);

  const update = useUpdateSetting();
  const reset = useResetSetting();

  const dirty = draft !== String(entry.value);
  const step = entry.type === "float" ? "0.05" : entry.type === "int" ? "1" : undefined;

  const onSave = async () => {
    try {
      await update.mutateAsync({ key: entry.key, value: draft });
      toast.success(`${entry.label} saved`);
    } catch (err) {
      toast.error((err as Error).message || "Save failed");
    }
  };

  const onReset = async () => {
    try {
      await reset.mutateAsync(entry.key);
      toast.success(`${entry.label} reset`);
    } catch (err) {
      toast.error((err as Error).message || "Reset failed");
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{entry.label}</span>
        <span
          className={cn(
            "rounded-sm px-1.5 py-0.5 text-[9px] font-mono font-semibold uppercase tracking-wide",
            SOURCE_STYLE[entry.source],
          )}
          title={`Effective value comes from ${entry.source === "db" ? "this database override" : entry.source === "env" ? "environment variable" : "hard-coded default"}`}
        >
          {entry.source}
        </span>
        {entry.source === "db" && entry.updated_at ? (
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            changed <RelativeTime value={entry.updated_at} />
            {entry.updated_by_username ? ` by ${entry.updated_by_username}` : ""}
          </span>
        ) : null}
      </div>
      <p className="mb-3 text-xs text-muted-foreground">{entry.description}</p>

      <div className="flex items-center gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          type={entry.type === "float" || entry.type === "int" ? "number" : "text"}
          step={step}
          className={cn(
            "h-9 font-mono text-sm",
            // Strings (hostnames, CIDR lists) need more room than the
            // fixed-width numeric inputs.
            entry.type === "string" ? "w-80" : "w-40",
          )}
        />
        <span className="font-mono text-[10px] text-muted-foreground">
          default {String(entry.default)}
        </span>
        <div className="ml-auto flex gap-2">
          {entry.source === "db" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onReset}
              disabled={reset.isPending}
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              Reset
            </Button>
          ) : null}
          <Button
            size="sm"
            onClick={onSave}
            disabled={!dirty || update.isPending}
          >
            <Save className="mr-1.5 h-3.5 w-3.5" />
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </div>
  );
}
