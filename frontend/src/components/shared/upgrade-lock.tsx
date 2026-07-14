import * as React from "react";
import { Check, Lock, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useFeatures } from "@/hooks/use-features";
import { cn } from "@/lib/utils";

// Conversion-oriented gating for Enterprise-only controls in the sandbox
// edition. Rather than hiding enterprise features (which makes the upgrade
// invisible), we LOCK them: the control stays on screen, greyed and
// non-interactive, clearly marked "Enterprise only". The visible-but-locked
// state is the upgrade trigger.

const DEFAULT_HINT = "Available in the KYDE Enterprise edition.";

/**
 * Wrap any interactive control (button, link). When `locked`, the child is
 * greyed, pointer-events are removed so it can't be clicked, a small lock
 * badge is overlaid, and a tooltip shows the upgrade hint. When not locked,
 * the child renders untouched.
 */
export function PaidLock({
  locked,
  hint,
  className,
  children,
}: {
  locked: boolean;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  if (!locked) return <>{children}</>;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "relative inline-flex cursor-not-allowed align-middle",
              className,
            )}
          >
            <span className="pointer-events-none block w-full opacity-50 grayscale">
              {children}
            </span>
            <Lock className="absolute -right-1.5 -top-1.5 h-3 w-3 rounded-full bg-card text-muted-foreground" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{hint ?? DEFAULT_HINT}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// External pricing/upgrade destination. Single source of truth so every CTA
// points at the same place.
export const UPGRADE_URL = "https://kyde.com/pricing/";

/**
 * Pinned sidebar upsell. Renders nothing in the enterprise edition; in the sandbox edition it
 * shows an always-visible card naming what Enterprise unlocks plus a CTA to
 * the pricing page. This is the primary, low-noise conversion surface.
 */
export function UpgradeCard() {
  const { isSandbox } = useFeatures();
  if (!isSandbox) return null;
  const unlocks = [
    "Inline enforcement",
    "Signed audit ledger",
    "Agent block-list & TPM",
  ];
  return (
    <div className="m-2 rounded-lg border border-brand-yellow/30 bg-brand-yellow/5 p-3">
      <div className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
        <Sparkles className="h-3.5 w-3.5 text-brand-yellow" />
        KYDE Sandbox
      </div>
      <p className="text-[11px] text-muted-foreground">
        Observe-only. Enterprise unlocks:
      </p>
      <ul className="mt-1.5 mb-2.5 space-y-1">
        {unlocks.map((u) => (
          <li
            key={u}
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
          >
            <Check className="h-3 w-3 shrink-0 text-brand-yellow" />
            {u}
          </li>
        ))}
      </ul>
      <a
        href={UPGRADE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="flex w-full items-center justify-center rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        Upgrade to Enterprise →
      </a>
    </div>
  );
}

/** Small inline "Enterprise" chip for nav items / section headers. */
export function PaidBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="tag-muted"
      className={cn("gap-1", className)}
      title="Available in the KYDE Enterprise edition"
    >
      <Lock className="h-2.5 w-2.5" />
      Enterprise
    </Badge>
  );
}

/**
 * MetricCard-shaped locked tile for Enterprise-only metrics (signature
 * failures, chain-integrity verification). Drops into the same grids as
 * MetricCard so a locked slot lines up with its live neighbours.
 */
export function LockedMetric({ label }: { label: string }) {
  return (
    <div
      className="rounded-md border border-dashed bg-card p-4"
      title={DEFAULT_HINT}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          {label}
        </span>
        <PaidBadge />
      </div>
      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <Lock className="h-3 w-3" />
        Enterprise only
      </div>
    </div>
  );
}

/** Larger locked panel for Enterprise-only detail cards. */
export function LockedPanel({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="rounded-lg border border-dashed bg-card p-5"
      title={DEFAULT_HINT}
    >
      <div className="mb-3 flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
        <PaidBadge />
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Lock className="h-4 w-4" />
        <span>{children ?? "Available in the Enterprise edition."}</span>
      </div>
    </div>
  );
}

/**
 * Full-section placeholder for enterprise-only pages (signing config, integrity).
 * Replaces the page body with an explanation + what upgrading unlocks.
 */
export function UpgradeNotice({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-dashed bg-card p-8 text-center">
      <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-muted">
        <Lock className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="mb-1 flex items-center justify-center gap-2">
        <h2 className="text-base font-semibold">{title}</h2>
        <PaidBadge />
      </div>
      <p className="mx-auto max-w-md text-sm text-muted-foreground">
        {children ??
          "This feature is part of KYDE Enterprise. The sandbox edition runs in observe-only mode — detection and alerts work, but enforcement and independent audit signing require an upgrade."}
      </p>
      <a
        href={UPGRADE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-4 inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        Upgrade to Enterprise →
      </a>
    </div>
  );
}
