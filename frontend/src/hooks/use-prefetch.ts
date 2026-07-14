import { useQueryClient } from "@tanstack/react-query";
import { DEFAULT_SESSION_FILTERS, qk } from "@/api/queries";
import { fetchJSON, qs } from "@/api/client";
import type {
  DlpAlert,
  EntriesPage,
  SessionsPage,
  Stats,
  TokenAnalysis,
  Verify,
} from "@/api/types";

// Preload a nav target's primary query when the user hovers its link.
// Makes click-through feel instant at the cost of one extra request
// per hover; TanStack Query dedupes and caches, so re-hovers are free.
export function useNavPrefetch() {
  const qc = useQueryClient();

  return (path: string) => {
    switch (path) {
      case "/":
        // Prefetch the same default window the overview uses so a single
        // request covers both the hover-prefetch and the actual page load.
        qc.prefetchQuery({
          queryKey: qk.stats("7d"),
          queryFn: () => fetchJSON<Stats>("/api/stats?window=7d"),
        });
        break;
      case "/integrity":
        qc.prefetchQuery({ queryKey: qk.verify, queryFn: () => fetchJSON<Verify>("/api/verify") });
        break;
      case "/timeline":
        qc.prefetchInfiniteQuery({
          queryKey: qk.entries({}),
          queryFn: () => fetchJSON<EntriesPage>("/api/entries" + qs({ limit: 50 })),
          initialPageParam: "" as string,
        });
        break;
      case "/sessions":
        // Match the Sessions page's default filter set (24h window, no
        // alert filter, no agent filter, newest first) so the prefetch
        // hits the same cache key the page will read.
        qc.prefetchInfiniteQuery({
          queryKey: qk.sessions(DEFAULT_SESSION_FILTERS),
          queryFn: () =>
            fetchJSON<SessionsPage>(
              "/api/sessions" +
                qs({
                  limit: 50,
                  window: DEFAULT_SESSION_FILTERS.window,
                  sort: DEFAULT_SESSION_FILTERS.sort,
                }),
            ),
          initialPageParam: "" as string,
        });
        break;
      case "/tokens":
        // Match the legacy /tokens page's window (30d).
        qc.prefetchQuery({
          queryKey: qk.tokenAnalysis("30d"),
          queryFn: () => fetchJSON<TokenAnalysis>("/api/token-analysis?window=30d"),
        });
        break;
      case "/dlp":
        qc.prefetchQuery({
          queryKey: qk.dlpAlerts,
          queryFn: () => fetchJSON<DlpAlert[]>("/api/dlp-alerts"),
        });
        break;
      // /users, /profile are cheap enough that a cold load is fine.
    }
  };
}
