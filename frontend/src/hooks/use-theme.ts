import { useCallback, useSyncExternalStore } from "react";

// Light/dark theme switch. The stored preference is explicit ("light" |
// "dark"); when nothing is stored we follow the OS. The resolved theme is
// applied as a `dark` class on <html> (Tailwind darkMode: ["class"]) — the
// same logic runs as an inline script in index.html so the first paint
// already has the right class (no flash).
const STORAGE_KEY = "kyde-theme";

export type Theme = "light" | "dark";

function readStored(): Theme | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "dark" || raw === "light" ? raw : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveTheme(): Theme {
  return readStored() ?? systemTheme();
}

function apply(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

// Tiny external store so every useTheme() consumer re-renders on change,
// including changes triggered from other components or other tabs.
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystem = () => {
    // Only relevant while following the OS (no explicit choice stored).
    if (!readStored()) {
      apply(systemTheme());
      emit();
    }
  };
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) {
      apply(resolveTheme());
      emit();
    }
  };
  media.addEventListener("change", onSystem);
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    media.removeEventListener("change", onSystem);
    window.removeEventListener("storage", onStorage);
  };
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, resolveTheme, () => "light" as Theme);

  const setTheme = useCallback((next: Theme) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode etc. — still switch for this page load.
    }
    apply(next);
    emit();
  }, []);

  const toggle = useCallback(() => {
    setTheme(resolveTheme() === "dark" ? "light" : "dark");
  }, [setTheme]);

  return { theme, setTheme, toggle };
}
