import { ChevronsUpDown, LogOut, User as UserIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/api/client";
import { useMe } from "@/hooks/use-me";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function UserMenu() {
  const { me, roles } = useMe();
  const navigate = useNavigate();

  const initials = (me?.username ?? "?")
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("") || "?";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="mx-2 mb-2 flex items-center gap-2 rounded-md border border-border bg-transparent px-2 py-2 text-left transition-colors hover:bg-accent/40">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 font-mono text-[11px] font-semibold text-primary">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-xs text-foreground">{me?.username ?? "—"}</div>
          <div className="flex gap-1 mt-0.5">
            {roles.slice(0, 2).map((r) => (
              <span
                key={r}
                className={cn(
                  "rounded-sm px-1 py-px text-[9px] font-mono uppercase tracking-wide",
                  r === "admin"
                    ? "bg-destructive/15 text-destructive"
                    : r === "auditor"
                    ? "bg-info/15 text-info"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {r}
              </span>
            ))}
          </div>
        </div>
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>

      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          Account
        </DropdownMenuLabel>
        <DropdownMenuItem onClick={() => navigate("/profile")}>
          <UserIcon className="mr-2 h-4 w-4" /> Profile
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <a href={API_BASE + "/logout"}>
            <LogOut className="mr-2 h-4 w-4" /> Sign out
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
