import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook } from "@testing-library/react";

// Stub the underlying TanStack query so we drive role derivation directly,
// without a QueryClient or network. vi.hoisted lets the mock factory below
// reference the spy despite vi.mock hoisting.
const { meQuery } = vi.hoisted(() => ({ meQuery: vi.fn() }));
vi.mock("@/api/queries", () => ({ useMe: () => meQuery() }));

import { useMe } from "./use-me";

beforeEach(() => {
  meQuery.mockReset();
});

describe("useMe role derivation", () => {
  it("marks an admin", () => {
    meQuery.mockReturnValue({ data: { roles: ["admin"] }, isLoading: false });
    const { result } = renderHook(() => useMe());
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isAuditor).toBe(false);
    expect(result.current.roles).toEqual(["admin"]);
    expect(result.current.me).toEqual({ roles: ["admin"] });
  });

  it("marks an auditor", () => {
    meQuery.mockReturnValue({ data: { roles: ["auditor"] }, isLoading: false });
    const { result } = renderHook(() => useMe());
    expect(result.current.isAuditor).toBe(true);
    expect(result.current.isAdmin).toBe(false);
  });

  it("supports holding both roles", () => {
    meQuery.mockReturnValue({
      data: { roles: ["admin", "auditor"] },
      isLoading: false,
    });
    const { result } = renderHook(() => useMe());
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isAuditor).toBe(true);
  });

  it("treats a viewer as neither admin nor auditor", () => {
    meQuery.mockReturnValue({ data: { roles: ["viewer"] }, isLoading: false });
    const { result } = renderHook(() => useMe());
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isAuditor).toBe(false);
  });

  it("treats a still-loading me as no roles (nothing privileged flashes pre-auth)", () => {
    meQuery.mockReturnValue({ data: undefined, isLoading: true });
    const { result } = renderHook(() => useMe());
    expect(result.current.roles).toEqual([]);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isAuditor).toBe(false);
  });
});
