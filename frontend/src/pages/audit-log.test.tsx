import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Spread the real module so exported constants (STATS_WINDOWS, defaults)
// survive; override only the hooks this page calls.
vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useEntriesInfinite: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, fetchNextPage: () => {}, hasNextPage: false, isFetchingNextPage: false }),
  useEntryFacets: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useEntryRef: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpAlerts: () => ({ data: [], isLoading: true }),
  useSessionsInfinite: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, fetchNextPage: () => {}, hasNextPage: false, isFetchingNextPage: false }),
  useVerify: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => ({ signingEnabled: true }) }));
vi.mock("@/hooks/use-agent-label", () => ({ useAgentLabel: () => ({ shortLabel: (s: string) => s }) }));

import AuditLogPage from "./audit-log";

describe("AuditLogPage", () => {
  it("renders the header in the loading state", () => {
    render(<MemoryRouter><AuditLogPage /></MemoryRouter>);
    expect(screen.getByText("Audit Log")).toBeInTheDocument();
  });
});
