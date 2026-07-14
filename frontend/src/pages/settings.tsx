import { NavLink, Outlet } from "react-router-dom";
import {
  Cpu,
  Database,
  FileClock,
  KeyRound,
  Mail,
  SlidersHorizontal,
} from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { useServiceMetrics } from "@/api/queries";
import { cn } from "@/lib/utils";

// Left-rail entries for the Settings shell. Order: most-frequented at
// the top (Overview is the landing screen so it stays first). Keep this
// list short — anything an operator edits regularly belongs in its own
// top-level nav entry, not buried under Settings.
const SECTIONS = [
  { to: "/settings", label: "Overview", icon: Cpu, end: true },
  { to: "/settings/runtime", label: "Runtime tuning", icon: SlidersHorizontal },
  { to: "/settings/email", label: "Email", icon: Mail },
  { to: "/settings/signing", label: "Signing", icon: KeyRound },
  { to: "/settings/ledger", label: "Ledger", icon: Database },
  { to: "/settings/admin-actions", label: "Admin Actions", icon: FileClock },
];

export default function SettingsLayout() {
  // dataUpdatedAt is shown in the page header so the user knows how
  // stale the surfaced metrics are. The hook is shared with the
  // overview sub-page so this doesn't trigger a second fetch.
  const { dataUpdatedAt } = useServiceMetrics();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Service configuration, signing, upstreams, and operational state"
        lastUpdated={dataUpdatedAt}
      />

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[200px_1fr]">
        <nav className="space-y-0.5">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <NavLink
                key={s.to}
                to={s.to}
                end={s.end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 rounded-md px-3 py-2 text-[13px] transition-colors",
                    isActive
                      ? "bg-accent font-medium text-foreground"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                  )
                }
              >
                <Icon className="h-4 w-4" />
                {s.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="min-w-0">
          <Outlet />
        </div>
      </div>
    </>
  );
}
