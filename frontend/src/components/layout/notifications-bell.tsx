import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDlpAlerts } from "@/api/queries";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DlpBadge } from "@/components/shared/dlp-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { truncate } from "@/lib/format";

// Client-side watermark: we remember the highest alert id we've seen so
// `unseen` falls to 0 after the user opens the tray. Not authoritative
// (doesn't persist server-side, doesn't survive a cleared browser), but
// good enough for a "oh, new alerts" nudge.
const WATERMARK_KEY = "dlp-watermark";

function readWatermark(): number {
  if (typeof window === "undefined") return 0;
  const raw = window.localStorage.getItem(WATERMARK_KEY);
  const n = raw ? Number.parseInt(raw, 10) : 0;
  return Number.isFinite(n) ? n : 0;
}

export function NotificationsBell() {
  const { data: alerts } = useDlpAlerts();
  const [watermark, setWatermark] = useState(readWatermark);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const maxId = useMemo(() => {
    if (!alerts || alerts.length === 0) return 0;
    return alerts.reduce((acc, a) => Math.max(acc, Number(a.id) || 0), 0);
  }, [alerts]);

  const unseenCount = useMemo(() => {
    if (!alerts) return 0;
    return alerts.filter((a) => (Number(a.id) || 0) > watermark).length;
  }, [alerts, watermark]);

  // Opening the tray marks everything up to the current max as seen.
  useEffect(() => {
    if (open && maxId > watermark) {
      window.localStorage.setItem(WATERMARK_KEY, String(maxId));
      setWatermark(maxId);
    }
  }, [open, maxId, watermark]);

  const recent = useMemo(() => {
    if (!alerts) return [];
    return [...alerts]
      .sort((a, b) => (Number(b.id) || 0) - (Number(a.id) || 0))
      .slice(0, 5);
  }, [alerts]);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        className="relative rounded-md border border-transparent p-1.5 text-muted-foreground hover:border-border hover:text-foreground"
        aria-label="Notifications"
      >
        <Bell className="h-4 w-4" />
        {unseenCount > 0 ? (
          <span
            className={cn(
              "absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full",
              "bg-destructive px-1 font-mono text-[9px] font-bold text-destructive-foreground",
            )}
          >
            {unseenCount > 9 ? "9+" : unseenCount}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            DLP alerts
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {alerts?.length ?? 0} total
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        {recent.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            No alerts yet.
          </div>
        ) : (
          recent.map((a) => (
            <DropdownMenuItem
              key={String(a.id)}
              className="flex-col items-start gap-1"
              onClick={() => {
                setOpen(false);
                navigate("/dlp");
              }}
            >
              <div className="flex w-full items-center gap-2">
                <DlpBadge status={String(a.status)} />
                <span className="font-mono text-[11px] text-muted-foreground">
                  {a.scanner}
                </span>
                <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                  <RelativeTime value={a.created_dt} />
                </span>
              </div>
              <div className="w-full truncate font-mono text-[11px] text-foreground">
                {truncate(a.alert_id ?? String(a.id), 36)}
              </div>
            </DropdownMenuItem>
          ))
        )}

        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => {
            setOpen(false);
            navigate("/dlp");
          }}
        >
          View all alerts
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
