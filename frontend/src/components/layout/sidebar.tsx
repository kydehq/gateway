import { NavLink } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  CircleDot,
  Crown,
  HardDrive,
  Link2,
  ListChecks,
  ListTree,
  Network,
  Settings as SettingsIcon,
  ShieldCheck,
  Tag,
  TrendingUp,
  Users,
  Waypoints,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useMe } from "@/hooks/use-me";
import { useNavPrefetch } from "@/hooks/use-prefetch";
import { UserMenu } from "./user-menu";
import { NotificationsBell } from "./notifications-bell";
import { ThemeToggle } from "./theme-toggle";
import { ChainStatusChip } from "./chain-status-chip";
import { UpgradeCard } from "@/components/shared/upgrade-lock";

type Role = "admin" | "auditor";

interface NavEntry {
  to: string;
  label: string;
  icon: typeof CircleDot;
  /** Who sees this item. Omitted = both admin and auditor (shared). */
  roles?: Role[];
}

interface NavSection {
  title: string;
  items: NavEntry[];
}

// One declarative nav. Visibility is per-item via `roles` (omitted = shared);
// sections render only if they have a visible item. Admins and auditors share
// most surfaces — auditors get the Configuration pages read-only (enforced in
// the pages + API), and only Administration is admin-only.
const NAV: NavSection[] = [
  {
    title: "Overview",
    items: [
      { to: "/workforce-status", label: "Workforce Status", icon: CircleDot },
      { to: "/threats-alerts",   label: "Threats & Alerts",  icon: AlertTriangle },
      { to: "/compliance",       label: "Compliance",        icon: ShieldCheck },
      { to: "/audit-log",        label: "Audit Log",         icon: ListTree },
    ],
  },
  {
    title: "Agents & Traffic",
    items: [
      { to: "/agent-chains",   label: "Agent Chains",   icon: Link2 },
      { to: "/sessions",       label: "Sessions",       icon: Workflow },
      { to: "/agent-activity", label: "Agent Activity", icon: Activity },
      { to: "/network-map",    label: "Network Map",    icon: Network },
      { to: "/agents",         label: "Agents",         icon: Users },
      { to: "/hosts",          label: "Hosts",          icon: HardDrive },
      { to: "/usage-cost",     label: "Token Usage",    icon: TrendingUp },
    ],
  },
  {
    title: "Configuration",
    items: [
      { to: "/routing",  label: "Routing",  icon: Waypoints },
      { to: "/policies", label: "Policies", icon: ListChecks },
      { to: "/labels",   label: "Labels",   icon: Tag },
    ],
  },
  {
    title: "Administration",
    items: [
      { to: "/users",    label: "Users",    icon: Crown,        roles: ["admin"] },
      { to: "/settings", label: "Settings", icon: SettingsIcon, roles: ["admin"] },
    ],
  },
];

function NavItem({ entry, prefetch }: { entry: NavEntry; prefetch: (to: string) => void }) {
  const Icon = entry.icon;
  return (
    <NavLink
      to={entry.to}
      end={false}
      onMouseEnter={() => prefetch(entry.to)}
      onFocus={() => prefetch(entry.to)}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 rounded-md border border-transparent px-3 py-2 text-[13px] font-medium transition-colors",
          isActive
            ? "border-border bg-accent text-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
        )
      }
    >
      <Icon className="h-4 w-4" />
      {entry.label}
    </NavLink>
  );
}

export function Sidebar() {
  const { isAdmin, isAuditor, isLoading } = useMe();
  const prefetch = useNavPrefetch();

  const subtitle = isLoading
    ? "Agent Control Plane"
    : isAdmin
    ? "Agent Governance Console"
    : isAuditor
    ? "Compliance Evidence System"
    : "Agent Control Plane";

  // An item with no `roles` is shared; otherwise the viewer must hold one of
  // the listed roles. Sections render only when they have a visible item.
  const canSee = (entry: NavEntry) =>
    !entry.roles ||
    entry.roles.some((r) => (r === "admin" ? isAdmin : r === "auditor" ? isAuditor : false));
  const sections = NAV
    .map((s) => ({ title: s.title, items: s.items.filter(canSee) }))
    .filter((s) => s.items.length > 0);

  return (
    <aside className="flex h-screen w-[320px] min-w-[320px] flex-col overflow-y-auto border-r border-border bg-card">
      <div className="border-b border-border px-5 py-6">
        <div className="mb-4 flex items-center justify-between">
          <a href="./" className="inline-block" aria-label="KYDE home">
            <img src="/logo-black.svg" alt="KYDE" className="h-7 w-auto dark:hidden" />
            <img src="/logo-white.svg" alt="KYDE" className="hidden h-7 w-auto dark:block" />
          </a>
          <div className="flex items-center gap-0.5">
            <ThemeToggle />
            <NotificationsBell />
          </div>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <ChainStatusChip />
        </div>
        <div className="text-[15px] font-semibold tracking-tight">Audit Dashboard</div>
        <div className="mt-1 font-mono text-xs tracking-wide text-muted-foreground">
          {subtitle}
        </div>
      </div>

      <nav className="flex-1 p-2">
        {sections.map((section, i) => (
          <div key={section.title} className={i > 0 ? "mt-5" : undefined}>
            <div className="eyebrow px-3 pb-1.5">{section.title}</div>
            {section.items.map((n) => (
              <NavItem key={n.to} entry={n} prefetch={prefetch} />
            ))}
          </div>
        ))}
      </nav>

      <UpgradeCard />
      <UserMenu />
    </aside>
  );
}
