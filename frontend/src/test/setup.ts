// Global Vitest setup, loaded once per test file via `setupFiles` in
// vite.config.ts. It does three things:
//   1. Registers jest-dom matchers (toBeInTheDocument, toHaveClass, …).
//   2. Unmounts React trees after every test so cases stay isolated.
//   3. Polyfills the browser APIs jsdom omits that Radix UI primitives
//      (tooltip, popover, select) touch on mount, so component renders
//      don't throw on missing globals.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// matchMedia — referenced by some Radix/responsive code paths.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

// ResizeObserver / IntersectionObserver — jsdom ships neither; Radix and the
// infinite-scroll hook construct them on mount.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

// Assign via an untyped view of window: the lib.dom typings declare these as
// always-present, so a typed `"X" in window` guard narrows window to `never`.
const w = window as unknown as Record<string, unknown>;
if (!w.ResizeObserver) w.ResizeObserver = NoopObserver;
if (!w.IntersectionObserver) w.IntersectionObserver = NoopObserver;
