import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { AdminAction, AdminActionsResponse } from "@/api/types";

const h = vi.hoisted(() => ({
  actions: {
    data: undefined as AdminActionsResponse | undefined,
    isLoading: false,
    isError: false,
    error: undefined as Error | undefined,
  },
  params: undefined as Record<string, unknown> | undefined,
}));
vi.mock("@/api/queries", () => ({
  useAdminActions: (params: Record<string, unknown>) => {
    h.params = params;
    return h.actions;
  },
}));

import AdminActionsPage from "./admin-actions";

let actionSeq = 0;
function action(overrides: Partial<AdminAction>): AdminAction {
  actionSeq += 1;
  return {
    id: actionSeq,
    actor_id: 1,
    actor_username: "admin",
    action: "mcp_server.create",
    resource_type: "mcp_server",
    resource_id: "srv1",
    before: null,
    after: { name: "srv1", upstream_url: "http://x" },
    created_at: "2026-07-01T10:00:00Z",
    ...overrides,
  };
}

function page(items: AdminAction[], total = items.length): AdminActionsResponse {
  return { items, total, limit: 100, offset: 0 };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminActionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  actionSeq = 0;
  h.actions = { data: page([]), isLoading: false, isError: false, error: undefined };
  h.params = undefined;
});

describe("AdminActionsPage — states", () => {
  it("shows skeleton rows while loading", () => {
    h.actions = { data: undefined, isLoading: true, isError: false, error: undefined };
    const { container } = renderPage();
    expect(screen.getByText("Admin Actions")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows an error row when the query fails", () => {
    h.actions = { data: undefined, isLoading: false, isError: true, error: new Error("boom") };
    renderPage();
    expect(screen.getByText(/Failed to load: boom/)).toBeInTheDocument();
  });

  it("shows the empty state when there are no actions", () => {
    renderPage();
    expect(screen.getByText("No admin actions match.")).toBeInTheDocument();
    expect(screen.getByText("0 actions")).toBeInTheDocument();
  });

  it("pluralizes the total correctly", () => {
    h.actions = { data: page([action({})]), isLoading: false, isError: false, error: undefined };
    renderPage();
    expect(screen.getByText("1 action")).toBeInTheDocument();
  });
});

describe("AdminActionsPage — rows and diff summaries", () => {
  it("renders actor, pill, resource, target, and dash fallbacks", () => {
    h.actions = {
      data: page([
        action({}),
        action({ actor_username: null, resource_id: null, action: "dlp_policy.update" }),
      ]),
      isLoading: false,
      isError: false,
      error: undefined,
    };
    renderPage();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("mcp_server.create")).toBeInTheDocument();
    expect(screen.getByText("dlp_policy.update")).toBeInTheDocument();
    expect(screen.getByText("srv1")).toBeInTheDocument();
    // Missing actor and resource_id both render as dashes.
    expect(screen.getAllByText("—").length).toBe(2);
  });

  it("summarises creates, deletes, updates, and no-ops", () => {
    h.actions = {
      data: page([
        action({ before: null, after: { a: 1, b: 2 } }),
        action({ before: null, after: {} }),
        action({ before: { a: 1 }, after: null }),
        action({ before: { enabled: true, url: "x" }, after: { enabled: false, url: "x" } }),
        action({ before: { a: 1 }, after: { a: 1 } }),
      ]),
      isLoading: false,
      isError: false,
      error: undefined,
    };
    renderPage();
    expect(screen.getByText("created (2 fields)")).toBeInTheDocument();
    expect(screen.getByText("created")).toBeInTheDocument();
    expect(screen.getByText("deleted")).toBeInTheDocument();
    expect(screen.getByText("changed enabled")).toBeInTheDocument();
    expect(screen.getByText("no-op")).toBeInTheDocument();
  });
});

describe("AdminActionsPage — filters and pagination", () => {
  it("passes the debounced action filter and resets to page one", async () => {
    renderPage();
    expect(h.params).toMatchObject({ action: null, resource_type: null, offset: 0 });
    await userEvent.type(
      screen.getByPlaceholderText("Filter by action (e.g. mcp_server.create)"),
      "delete",
    );
    await waitFor(() => expect(h.params).toMatchObject({ action: "delete", offset: 0 }));
  });

  it("pages through results with Next/Previous", async () => {
    h.actions = { data: page([action({})], 250), isLoading: false, isError: false, error: undefined };
    renderPage();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    const prev = screen.getByRole("button", { name: "Previous" });
    const next = screen.getByRole("button", { name: "Next" });
    expect(prev).toBeDisabled();
    await userEvent.click(next);
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(h.params).toMatchObject({ offset: 100 });
    await userEvent.click(next);
    expect(screen.getByText("Page 3 of 3")).toBeInTheDocument();
    expect(next).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(h.params).toMatchObject({ offset: 100 });
  });
});
