import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", () => ({
  useEntriesInfinite: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, fetchNextPage: () => {}, hasNextPage: false, isFetchingNextPage: false }),
  useEntryFacets: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useEntryRef: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));

import TimelinePage from "./timeline";

describe("TimelinePage", () => {
  it("renders the header in the loading state", () => {
    render(<MemoryRouter><TimelinePage /></MemoryRouter>);
    expect(screen.getByText("Entry Timeline")).toBeInTheDocument();
  });
});
