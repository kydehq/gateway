import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  users: { data: undefined as unknown, isLoading: true, isError: false, error: undefined as unknown },
  me: { me: { user_id: 1 } },
}));
vi.mock("@/api/queries", () => ({
  useUsers: () => h.users,
  useDeleteUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResetUserPassword: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUnlockUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// The create/edit dialog has its own hooks + tests; stub it here.
vi.mock("@/components/shared/users-dialog", () => ({
  UsersDialog: () => null,
}));

import UsersPage from "./users";

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.users = { data: undefined, isLoading: true, isError: false, error: undefined };
  h.me = { me: { user_id: 1 } };
});

describe("UsersPage", () => {
  it("renders the header and Add user action", () => {
    renderPage();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add user/ })).toBeInTheDocument();
  });

  it("renders rows with roles and marks the current user as 'you'", () => {
    h.users = {
      data: [
        { id: 1, username: "alice", email: "a@x.test", roles: ["admin"], created_at: 1, status: "active" },
        { id: 2, username: "bob", email: "b@x.test", roles: ["viewer"], created_at: 2, status: "active" },
      ],
      isLoading: false,
      isError: false,
      error: undefined,
    };
    renderPage();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    // role chips (now unambiguous — no username collides with a role name)
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("viewer")).toBeInTheDocument();
    // self badge (alice is user_id 1)
    expect(screen.getByText("you")).toBeInTheDocument();
  });

  it("renders an error row when the query fails", () => {
    h.users = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("nope"),
    };
    renderPage();
    expect(screen.getByText(/Failed to load users: nope/)).toBeInTheDocument();
  });

  it("renders an empty state when there are no users", () => {
    h.users = { data: [], isLoading: false, isError: false, error: undefined };
    renderPage();
    expect(screen.getByText("No users.")).toBeInTheDocument();
  });
});
