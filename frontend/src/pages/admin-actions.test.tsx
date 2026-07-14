import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  actions: { data: undefined as unknown, isLoading: true, isError: false, error: undefined as unknown },
}));
vi.mock("@/api/queries", () => ({ useAdminActions: () => h.actions }));

import AdminActionsPage from "./admin-actions";

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminActionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.actions = { data: undefined, isLoading: true, isError: false, error: undefined };
});

describe("AdminActionsPage", () => {
  it("renders the header", () => {
    renderPage();
    expect(screen.getByText("Admin Actions")).toBeInTheDocument();
  });

  it("shows an error row when the query fails", () => {
    h.actions = { data: undefined, isLoading: false, isError: true, error: new Error("boom") };
    renderPage();
    expect(screen.getByText(/Failed to load: boom/)).toBeInTheDocument();
  });

  it("shows the empty state when there are no actions", () => {
    h.actions = { data: { items: [], total: 0 }, isLoading: false, isError: false, error: undefined };
    renderPage();
    expect(screen.getByText("No admin actions match.")).toBeInTheDocument();
  });

  it("renders an action pill for a returned row", () => {
    h.actions = {
      data: {
        items: [
          {
            id: 1,
            action: "mcp_server.create",
            resource_type: "mcp_server",
            resource_id: "srv1",
            actor_username: "admin",
            created_at: 1,
            detail: null,
          },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
      error: undefined,
    };
    renderPage();
    expect(screen.getByText("mcp_server.create")).toBeInTheDocument();
  });
});
