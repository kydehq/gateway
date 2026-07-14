import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useAgents: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useBlockAgent: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useUnblockAgent: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useUpdateAgent: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useFleetTrust: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useTokenAnalysis: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));

import AgentDetailPage from "./agent-detail";

describe("AgentDetailPage", () => {
  it("mounts without crashing", () => {
    const client = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter><AgentDetailPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(container).toBeTruthy();
  });
});
