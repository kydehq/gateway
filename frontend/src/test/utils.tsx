import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

// A QueryClient tuned for tests: no retries (so a rejected queryFn surfaces
// immediately instead of backing off) and no garbage-collection delay.
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface Options {
  /** Initial MemoryRouter entry. */
  route?: string;
  /** Reuse a specific client (e.g. to seed cache); otherwise a fresh one. */
  client?: QueryClient;
}

// Render a unit under the same providers the app mounts: TanStack Query +
// a MemoryRouter. Returns the RTL result plus the client for cache seeding.
export function renderWithProviders(ui: ReactElement, opts: Options = {}) {
  const client = opts.client ?? makeTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[opts.route ?? "/"]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return { client, ...render(ui, { wrapper }) };
}
