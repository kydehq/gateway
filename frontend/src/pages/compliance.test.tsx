import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useConfiguration: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpHealth: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useVerificationRuns: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useVerify: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));

import CompliancePage from "./compliance";

describe("CompliancePage", () => {
  it("renders the header", () => {
    render(<MemoryRouter><CompliancePage /></MemoryRouter>);
    expect(screen.getByText("Compliance")).toBeInTheDocument();
  });
});
