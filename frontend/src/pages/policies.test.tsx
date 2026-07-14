import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  usePolicies: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  usePreventionBulk: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useResyncPolicies: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useTogglePolicy: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useUpdateSetting: () => ({ mutateAsync: () => Promise.resolve(), mutate: () => {}, isPending: false }),
  useSettings: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));

import PoliciesPage from "./policies";

describe("PoliciesPage", () => {
  it("renders the header", () => {
    render(<MemoryRouter><PoliciesPage /></MemoryRouter>);
    expect(screen.getByText("Policies")).toBeInTheDocument();
  });
});
