import { NavLink, Outlet } from "react-router-dom";
import { Plug, Server } from "lucide-react";
import { cn } from "@/lib/utils";

// Shell for the two routing tables — LLM upstreams (read-only, from config)
// and MCP servers (editable). Same left-rail pattern as Settings, so "where
// does traffic go" is one prominent destination instead of scattered nav
// entries. Each sub-page renders its own header/actions in the content column.
const SECTIONS = [
  { to: "/routing", label: "LLM Providers", icon: Server, end: true },
  { to: "/routing/mcp-servers", label: "MCP Servers", icon: Plug },
];

export default function RoutingLayout() {
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[200px_1fr]">
      <nav className="space-y-0.5">
        <div className="eyebrow px-3 pb-1.5">Routing</div>
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
  );
}
