import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/api/queries", () => ({
  useConfiguration: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useServiceMetrics: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));

import SettingsOverviewPage from "./overview";

describe("SettingsOverviewPage", () => {
  it("renders the System section heading", () => {
    render(<SettingsOverviewPage />);
    expect(screen.getByText("System")).toBeInTheDocument();
  });
});
