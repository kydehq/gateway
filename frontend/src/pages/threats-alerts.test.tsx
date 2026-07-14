import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", () => ({
  useDlpAlerts: () => ({ data: [], isLoading: true }),
  useDlpAlert: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useBlockAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTransitionDlpAlert: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTogglePolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));
vi.mock("@/hooks/use-agent-label", () => ({ useAgentLabel: () => ({ shortLabel: (s: string) => s }) }));

import ThreatsAlertsPage from "./threats-alerts";

describe("ThreatsAlertsPage", () => {
  it("renders the header in the loading state", () => {
    render(<MemoryRouter><ThreatsAlertsPage /></MemoryRouter>);
    expect(screen.getByText("Threats & Alerts")).toBeInTheDocument();
  });
});
