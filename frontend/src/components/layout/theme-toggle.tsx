import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/hooks/use-theme";

// Sun/moon switch in the sidebar header — same quiet button language as the
// notifications bell next to it.
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      onClick={toggle}
      className="rounded-md border border-transparent p-1.5 text-muted-foreground hover:border-border hover:text-foreground"
      aria-label={label}
      title={label}
    >
      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
