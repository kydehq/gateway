import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import {
  AlertTriangle,
  CircleDot,
  Hash,
  ListTree,
  ShieldCheck,
  TrendingUp,
  User as UserIcon,
  Workflow,
  Crown,
  Settings as SettingsIcon,
} from "lucide-react";
import { useMe } from "@/hooks/use-me";
import { useEntryRef } from "@/hooks/use-entry-ref";

const PAGES = [
  { to: "/",          label: "Overview",       icon: CircleDot },
  { to: "/integrity", label: "Data Integrity", icon: ShieldCheck },
  { to: "/timeline",  label: "Entry Timeline", icon: ListTree },
  { to: "/sessions",  label: "Sessions",       icon: Workflow },
  { to: "/tokens",    label: "Token Analysis", icon: TrendingUp },
  { to: "/dlp",       label: "DLP Alerts",     icon: AlertTriangle },
  { to: "/profile",   label: "Profile",        icon: UserIcon },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const { isAdmin } = useMe();
  const { open: openEntry } = useEntryRef();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((p) => !p);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  const seqMatch = q.trim().match(/^#?(\d+)$/);

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Jump to page, or type #123 for an entry…" value={q} onValueChange={setQ} />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>

        {seqMatch ? (
          <>
            <CommandGroup heading="Jump to entry">
              <CommandItem
                onSelect={() => {
                  setOpen(false);
                  openEntry(seqMatch[1]);
                }}
              >
                <Hash className="mr-2 h-4 w-4" />
                Open entry #{seqMatch[1]}
              </CommandItem>
            </CommandGroup>
            <CommandSeparator />
          </>
        ) : null}

        <CommandGroup heading="Pages">
          {PAGES.map((p) => {
            const Icon = p.icon;
            return (
              <CommandItem key={p.to} onSelect={() => go(p.to)}>
                <Icon className="mr-2 h-4 w-4" />
                {p.label}
              </CommandItem>
            );
          })}
          {isAdmin ? (
            <>
              <CommandItem onSelect={() => go("/users")}>
                <Crown className="mr-2 h-4 w-4" />
                Users
              </CommandItem>
              <CommandItem onSelect={() => go("/settings")}>
                <SettingsIcon className="mr-2 h-4 w-4" />
                Settings
              </CommandItem>
            </>
          ) : null}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
