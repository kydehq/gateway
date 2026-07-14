import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useHostResolve: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDeleteHostLabel: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useUpsertHostLabel: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useTopologyIp: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));

import HostDetailPage from "./host-detail";

describe("HostDetailPage", () => {
  it("mounts without crashing", () => {
    const client = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter><HostDetailPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(container).toBeTruthy();
  });
});
