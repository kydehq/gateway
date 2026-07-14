import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

// Drive the guard by stubbing the me query; the real useMe role derivation
// and the real RequireAdmin redirect logic both still run.
const { meQuery } = vi.hoisted(() => ({ meQuery: vi.fn() }));
vi.mock("@/api/queries", () => ({ useMe: () => meQuery() }));

import { RequireAdmin } from "./require-admin";

function renderGuard() {
  return render(
    <MemoryRouter initialEntries={["/admin"]}>
      <Routes>
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <div>ADMIN AREA</div>
            </RequireAdmin>
          }
        />
        <Route path="/" element={<div>HOME</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  meQuery.mockReset();
});

describe("RequireAdmin", () => {
  it("renders children for an admin", () => {
    meQuery.mockReturnValue({ data: { roles: ["admin"] }, isLoading: false });
    renderGuard();
    expect(screen.getByText("ADMIN AREA")).toBeInTheDocument();
    expect(screen.queryByText("HOME")).not.toBeInTheDocument();
  });

  it("redirects a non-admin to /", () => {
    meQuery.mockReturnValue({ data: { roles: ["auditor"] }, isLoading: false });
    renderGuard();
    expect(screen.getByText("HOME")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN AREA")).not.toBeInTheDocument();
  });

  it("shows neither children nor a redirect while loading", () => {
    meQuery.mockReturnValue({ data: undefined, isLoading: true });
    renderGuard();
    expect(screen.queryByText("ADMIN AREA")).not.toBeInTheDocument();
    expect(screen.queryByText("HOME")).not.toBeInTheDocument();
  });
});
