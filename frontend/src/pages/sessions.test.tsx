import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useSessionsInfinite: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, fetchNextPage: () => {}, hasNextPage: false, isFetchingNextPage: false }),
  useSession: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpAlert: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
  useDlpAlerts: () => ({ data: [], isLoading: true }),
  useEntryRef: () => ({ data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => ({ me: undefined, isAdmin: false }) }));

import SessionsPage from "./sessions";

describe("SessionsPage", () => {
  it("mounts the sessions view in the loading state", () => {
    const { container } = render(<MemoryRouter><SessionsPage /></MemoryRouter>);
    expect(container.firstChild).toBeTruthy();
  });
});
