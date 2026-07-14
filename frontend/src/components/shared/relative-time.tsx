import { useEffect, useState } from "react";
import { formatDistanceToNowStrict } from "date-fns";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// Accepts either an ISO-ish string ("2026-04-21 11:40:10", with or
// without a Z/offset — space-separated is treated as UTC) or a Unix
// timestamp. For numbers we infer seconds vs milliseconds by magnitude:
// anything under ~10^12 is seconds (covers up through the year 33658).
function toDate(raw: string | number | null | undefined): Date | null {
  if (raw == null || raw === "") return null;
  if (typeof raw === "number") {
    const ms = raw < 1e12 ? raw * 1000 : raw;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  // Pure-numeric string (e.g. "1776769535.355") → same unix path.
  if (/^\d+(\.\d+)?$/.test(raw)) {
    const n = Number(raw);
    const ms = n < 1e12 ? n * 1000 : n;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const s = /[Zz]$|[+-]\d\d:?\d\d$/.test(raw) ? raw : raw.replace(" ", "T") + "Z";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

const ABSOLUTE_FMT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
});

export function formatAbsolute(raw: string | number | null | undefined): string {
  const d = toDate(raw);
  return d ? ABSOLUTE_FMT.format(d) : "-";
}

// Re-render once a minute so the relative label stays fresh for the
// "just now" / "3 min ago" window without burning work on older entries.
function useTick(intervalMs: number) {
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

export function RelativeTime({
  value,
  className,
}: {
  value: string | number | null | undefined;
  className?: string;
}) {
  useTick(60_000);
  const d = toDate(value);
  if (!d) return <span className={className}>-</span>;

  const relative = formatDistanceToNowStrict(d, { addSuffix: true });
  const absolute = ABSOLUTE_FMT.format(d);

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("cursor-help font-mono", className)}>{relative}</span>
        </TooltipTrigger>
        <TooltipContent>
          <span className="font-mono text-xs">{absolute}</span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
