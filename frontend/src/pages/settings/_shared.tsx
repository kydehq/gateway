import type { ReactNode } from "react";
import { CopyButton } from "@/components/shared/copy-button";
import { cn } from "@/lib/utils";

// Small helpers shared across the Settings sub-pages. Kept private to
// the settings/ folder so they don't grow into a generic utility module
// that other pages start coupling to.

export function KeyValueRow({
  label,
  value,
  mono = true,
  copyable,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  copyable?: string;
}) {
  return (
    <div className="flex items-start gap-2 border-b border-border/50 py-2 last:border-0">
      <div className="w-40 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={cn("flex-1 break-all text-xs", mono && "font-mono")}>
        {value}
      </div>
      {copyable ? (
        <CopyButton value={copyable} label={label.toLowerCase()} />
      ) : null}
    </div>
  );
}

export function fmtBytes(n: number): string {
  if (!Number.isFinite(n)) return "-";
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(2) + " GB";
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(2) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(2) + " KB";
  return n + " B";
}

export function fmtUptime(sec: number): string {
  if (!Number.isFinite(sec)) return "-";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}
