import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useSessionsInfinite: () => ({ data: undefined, isLoading: true, isError: false, fetchNextPage: () => {}, hasNextPage: false, isFetchingNextPage: false }),
  useSession: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpAlert: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useEntryRef: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));
vi.mock("@/hooks/use-agent-label", () => ({ useAgentLabel: () => ({ shortLabel: (s: string) => s }) }));

import AgentChainsPage from "./agent-chains";

describe("AgentChainsPage", () => {
  it("renders the header", () => {
    render(<MemoryRouter><AgentChainsPage /></MemoryRouter>);
    expect(screen.getByText("Agent Chains")).toBeInTheDocument();
  });
});
