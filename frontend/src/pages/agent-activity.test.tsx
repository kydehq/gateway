import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useAgents: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useBlockAgent: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useDlpAlerts: () => ({ data: [], isLoading: true }),
  useStats: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useTokenAnalysis: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useTopologyAgent: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));
vi.mock("@/hooks/use-agent-label", () => ({ useAgentLabel: () => ({ shortLabel: (s: string) => s }) }));

import AgentActivityPage from "./agent-activity";

describe("AgentActivityPage", () => {
  it("renders the header", () => {
    render(<MemoryRouter><AgentActivityPage /></MemoryRouter>);
    expect(screen.getByText("Agent Activity")).toBeInTheDocument();
  });
});
