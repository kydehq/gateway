import { useState } from "react";
import { format } from "date-fns";
import type { DateRange } from "react-day-picker";
import { CalendarIcon, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type { DateRange };

interface Props {
  value: DateRange | undefined;
  onChange: (range: DateRange | undefined) => void;
  className?: string;
}

export function DateRangePicker({ value, onChange, className }: Props) {
  const [open, setOpen] = useState(false);
  const label =
    value?.from && value?.to
      ? `${format(value.from, "MMM d")} – ${format(value.to, "MMM d")}`
      : value?.from
      ? `From ${format(value.from, "MMM d, yyyy")}`
      : "All time";

  return (
    <div className={cn("inline-flex items-center gap-1", className)}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 text-xs">
            <CalendarIcon className="mr-2 h-3.5 w-3.5" />
            {label}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="end">
          <Calendar
            mode="range"
            defaultMonth={value?.from}
            selected={value}
            onSelect={(r) => onChange(r)}
            numberOfMonths={2}
          />
        </PopoverContent>
      </Popover>
      {value?.from || value?.to ? (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={() => onChange(undefined)}
          aria-label="Clear date range"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      ) : null}
    </div>
  );
}

// Apply a range to a Record<date-string, V>. Date strings are expected to
// match at prefix length `keyLen` (e.g. 10 for "YYYY-MM-DD", 13 for
// "YYYY-MM-DDTHH"). Keys outside the range are filtered out.
export function filterByRange<V>(
  obj: Record<string, V>,
  range: DateRange | undefined,
  keyLen: number,
): Record<string, V> {
  if (!range?.from) return obj;
  const fromKey = format(range.from, keyLen === 10 ? "yyyy-MM-dd" : "yyyy-MM-dd'T'HH");
  const toKey = range.to
    ? format(range.to, keyLen === 10 ? "yyyy-MM-dd" : "yyyy-MM-dd'T'HH")
    : fromKey;
  const out: Record<string, V> = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = k.slice(0, keyLen);
    if (key >= fromKey && key <= toKey) out[k] = v;
  }
  return out;
}
