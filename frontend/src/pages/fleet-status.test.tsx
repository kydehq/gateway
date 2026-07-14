import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useStats: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useVerify: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useFleetTrust: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpAlerts: () => ({ data: [], isLoading: true }),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));
vi.mock("@/hooks/use-agent-label", () => ({ useAgentLabel: () => ({ shortLabel: (s: string) => s }) }));

import FleetStatusPage from "./fleet-status";

describe("FleetStatusPage", () => {
  it("renders the header", () => {
    render(<MemoryRouter><FleetStatusPage /></MemoryRouter>);
    expect(screen.getByText("Workforce Status")).toBeInTheDocument();
  });
});
