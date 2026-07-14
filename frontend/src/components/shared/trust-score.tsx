import { cn } from "@/lib/utils";
import type { TrustDimensions, TrustTierKey } from "@/api/types";

// The five formula dimensions that produce the composite score, in formula
// order (Safety Gate inputs first, then the Operational Score inputs). Shared
// by the Workforce Status hero and the agent-detail block.
export const TRUST_DIMENSIONS: { key: keyof TrustDimensions; label: string }[] = [
  { key: "security",    label: "Security" },
  { key: "compliance",  label: "Compliance" },
  { key: "integrity",   label: "Integrity" },
  { key: "reliability", label: "Reliability" },
  { key: "economics",   label: "Economics" },
];

// Tier → on-palette severity color. Trust runs the severity axis backwards
// from threats (high score = good), so Autonomous borrows the LOW/green pair
// and Isolated the CRITICAL/red pair.
const TIER: Record<TrustTierKey, { label: string; text: string; dot: string; border: string }> = {
  autonomous:     { label: "Autonomous",     text: "text-sev-low",      dot: "bg-sev-low",      border: "border-sev-low" },
  monitored:      { label: "Monitored",      text: "text-sev-medium",   dot: "bg-sev-medium",   border: "border-sev-medium" },
  human_approval: { label: "Human Approval", text: "text-sev-high",     dot: "bg-sev-high",     border: "border-sev-high" },
  isolated:       { label: "Isolated",       text: "text-sev-critical", dot: "bg-sev-critical", border: "border-sev-critical" },
};

// Bright red → orange → light yellow → green, left (0) to right (100). The
// gauge gets its own ramp rather than the dark `--sev-*-fg` badge tokens: a
// short red shoulder, a luminous yellow center, and a fresh green — so the
// scale reads as a spectrum, not a dramatic alert band.
const GRADIENT =
  "linear-gradient(to right, hsl(4 84% 57%) 0%, hsl(28 92% 56%) 28%, hsl(48 96% 56%) 50%, hsl(86 64% 50%) 72%, hsl(142 62% 44%) 100%)";

export function tierMeta(tierKey: TrustTierKey) {
  return TIER[tierKey];
}

const clamp = (n: number) => Math.max(0, Math.min(100, n));

// A 0–100 value mapped onto the same red→green scale as the gauge, so a
// per-dimension fill bar reads as a position on that scale. Hue 0 (red) → 135
// (green); kept muted to stay on-brand with the flat severity palette.
function scaleColor(value: number) {
  const hue = (clamp(value) / 100) * 135;
  return `hsl(${hue.toFixed(0)} 52% 40%)`;
}

/**
 * The wide labeled trust gauge: red→green track with CRITICAL / AT RISK /
 * HEALTHY zone labels above, a ▼ marker at the score, and a 0–50–100 scale
 * below. Pure CSS + design tokens — no chart dependency.
 */
function TrustGauge({ score, tierKey }: { score: number; tierKey: TrustTierKey }) {
  const pos = clamp(score);
  const meta = TIER[tierKey];
  return (
    <div className="w-full">
      <div className="flex justify-between font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
        <span>Critical</span>
        <span>At Risk</span>
        <span>Trusted</span>
      </div>
      <div className="relative mt-1.5">
        {/* Downward marker triangle sitting on top of the track. */}
        <div
          className="absolute -top-1.5 -translate-x-1/2"
          style={{ left: `${pos}%` }}
          aria-hidden
        >
          <div
            className={cn("h-0 w-0", meta.text)}
            style={{
              borderLeft: "5px solid transparent",
              borderRight: "5px solid transparent",
              borderTop: "6px solid currentColor",
            }}
          />
        </div>
        <div className="h-2.5 w-full rounded-full" style={{ background: GRADIENT }} />
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>
    </div>
  );
}

/**
 * Per-dimension scales: UPPERCASE label + value on a row, with a filled
 * progress bar beneath, colored by the value's position on the trust scale.
 */
export function DimensionBars({
  dimensions,
  className,
}: {
  dimensions: TrustDimensions;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3", className)}>
      {TRUST_DIMENSIONS.map(({ key, label }) => {
        const value = clamp(dimensions[key]);
        return (
          <div key={key}>
            <div className="flex items-baseline justify-between">
              <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {label}
              </span>
              <span className="font-mono text-sm font-semibold tabular-nums">{Math.round(value)}</span>
            </div>
            <div
              className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full"
              style={{ background: "hsl(var(--chart-track))" }}
            >
              <div
                className="h-full rounded-full"
                style={{ width: `${value}%`, background: scaleColor(value) }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

interface HeroProps {
  score: number;
  tierKey: TrustTierKey;
  /** Defaults to the canonical tier label; pass to override (e.g. backend tier). */
  tier?: string;
  /** Small line beside the tier pill, e.g. "across 12 active agents". */
  caption?: string;
  /** Eyebrow label above the score. */
  label?: string;
  /** When present, renders the per-dimension scales below a divider. */
  dimensions?: TrustDimensions;
  /** Tighter layout for the agent-detail page. */
  compact?: boolean;
}

/**
 * The Fleet/Agent Trust Score hero: big NN/100 + tier pill on the left, the
 * labeled red→green gauge on the right, and (optionally) the per-dimension
 * scales below. Used full-width on Workforce Status and compact on agent detail.
 */
export function TrustScoreHero({
  score,
  tierKey,
  tier,
  caption,
  label = "Fleet Trust Score",
  dimensions,
  compact,
}: HeroProps) {
  const meta = TIER[tierKey];
  return (
    <div className={cn("rounded-lg border bg-card", compact ? "p-4" : "p-5")}>
      <div className="eyebrow">{label}</div>

      <div className="mt-3 grid items-center gap-6 lg:grid-cols-[auto_1fr]">
        {/* Score + tier pill */}
        <div>
          <div className="flex items-baseline gap-1.5">
            <span
              className={cn(
                "font-mono font-semibold tracking-[-0.02em] tabular-nums leading-none",
                meta.text,
                compact ? "text-[44px]" : "text-[64px]",
              )}
            >
              {score}
            </span>
            <span className="text-xl text-muted-foreground">/100</span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1 text-xs font-semibold",
                meta.text,
                meta.border,
              )}
            >
              <span className={cn("h-2 w-2 rounded-full", meta.dot)} />
              {tier ?? meta.label}
            </span>
            {caption ? <span className="text-sm text-muted-foreground">{caption}</span> : null}
          </div>
        </div>

        {/* Labeled gauge */}
        <TrustGauge score={score} tierKey={tierKey} />
      </div>

      {dimensions ? (
        <>
          <div className="my-5 border-t border-border" />
          <DimensionBars dimensions={dimensions} />
        </>
      ) : null}
    </div>
  );
}

/**
 * Compact score + tier dot for table cells (Agents list). No bar.
 */
export function TrustBadge({ score, tierKey }: { score: number; tierKey: TrustTierKey }) {
  const meta = TIER[tierKey];
  return (
    <span className="inline-flex items-center gap-2 font-mono text-xs tabular-nums" title={meta.label}>
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {score}
    </span>
  );
}
