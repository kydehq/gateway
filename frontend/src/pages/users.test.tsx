import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { User } from "@/api/types";

const h = vi.hoisted(() => ({
  users: {
    data: undefined as User[] | undefined,
    isLoading: false,
    isError: false,
    error: undefined as Error | undefined,
  },
  includeDeleted: undefined as boolean | undefined,
  del: { mutateAsync: vi.fn(), isPending: false },
  reset: { mutateAsync: vi.fn(), isPending: false },
  unlock: { mutateAsync: vi.fn(), isPending: false },
  me: { me: { user_id: 1 } },
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/api/queries", () => ({
  useUsers: (includeDeleted: boolean) => {
    h.includeDeleted = includeDeleted;
    return h.users;
  },
  useDeleteUser: () => h.del,
  useResetUserPassword: () => h.reset,
  useUnlockUser: () => h.unlock,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("sonner", () => ({ toast: h.toast }));
// The create/edit dialog has its own hooks + tests; stub it with a marker
// so this page test can assert the open/user wiring only.
vi.mock("@/components/shared/users-dialog", () => ({
  UsersDialog: ({ open, user }: { open: boolean; user?: User }) =>
    open ? <div>{`dialog:${user?.username ?? "new"}`}</div> : null,
}));

import UsersPage from "./users";

function user(overrides: Partial<User>): User {
  return {
    id: 1,
    username: "alice",
    email: "a@x.test",
    roles: ["admin"],
    status: "active",
    created_at: 1750000000,
    deleted_at: null,
    ...overrides,
  };
}

const USERS: User[] = [
  user({ id: 1, username: "alice", roles: ["admin"], created_at: 3 }),
  user({ id: 2, username: "bob", email: "b@x.test", roles: ["auditor"], created_at: 2 }),
  user({ id: 3, username: "zoe", email: undefined, roles: [], created_at: 1, status: undefined }),
];

const writeText = vi.fn();

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersPage />
    </MemoryRouter>,
  );
}

function usernameColumn(): Array<string | null> {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].textContent);
}

async function openRowMenu(rowIndex: number) {
  await userEvent.click(
    screen.getAllByRole("button", { name: "User actions" })[rowIndex],
  );
}

beforeEach(() => {
  const el = Element.prototype as unknown as Record<string, unknown>;
  el.hasPointerCapture ??= () => false;
  el.setPointerCapture ??= () => {};
  el.releasePointerCapture ??= () => {};
  el.scrollIntoView ??= () => {};

  h.users = { data: USERS, isLoading: false, isError: false, error: undefined };
  h.includeDeleted = undefined;
  h.del = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.reset = {
    mutateAsync: vi.fn().mockResolvedValue({ temp_password: "s3cret-temp" }),
    isPending: false,
  };
  h.unlock = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.me = { me: { user_id: 1 } };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
  writeText.mockReset();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

describe("UsersPage — table states", () => {
  it("shows skeleton rows while loading", () => {
    h.users = { data: undefined, isLoading: true, isError: false, error: undefined };
    const { container } = renderPage();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
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

  it("renders rows with roles, self badge, and dash fallbacks", () => {
    renderPage();
    // Default sort: username asc.
    expect(usernameColumn()).toEqual(["aliceyou", "bob", "zoe"]);
    expect(screen.getByText("you")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("auditor")).toBeInTheDocument();
    // zoe: no roles → "—", no email → "-", no status → "-".
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("marks a deleted user via the deleted_at fallback", () => {
    h.users = {
      data: [user({ id: 9, username: "ghost", status: undefined, deleted_at: 100 })],
      isLoading: false,
      isError: false,
      error: undefined,
    };
    renderPage();
    expect(screen.getByText("deleted")).toBeInTheDocument();
  });

  it("passes the Show-deleted toggle to the query", async () => {
    renderPage();
    expect(h.includeDeleted).toBe(false);
    await userEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(h.includeDeleted).toBe(true));
  });

  it("flips the username sort and sorts by created date", async () => {
    renderPage();
    await userEvent.click(screen.getByText("Username"));
    expect(usernameColumn()).toEqual(["zoe", "bob", "aliceyou"]);
    // Created: first click sorts asc by timestamp (zoe oldest).
    await userEvent.click(screen.getByText("Created"));
    expect(usernameColumn()[0]).toBe("zoe");
  });
});

describe("UsersPage — dialogs and actions", () => {
  it("opens the add dialog from the header", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Add user/ }));
    expect(screen.getByText("dialog:new")).toBeInTheDocument();
  });

  it("opens the edit dialog from the row menu", async () => {
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Edit"));
    expect(screen.getByText("dialog:bob")).toBeInTheDocument();
  });

  it("resets a password and shows the temp-password dialog with copy", async () => {
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Reset password"));
    await waitFor(() => expect(h.reset.mutateAsync).toHaveBeenCalledWith(2));
    expect(h.toast.success).toHaveBeenCalledWith("Password reset for bob");
    expect(screen.getByText("Temporary password")).toBeInTheDocument();
    expect(screen.getByText("s3cret-temp")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("s3cret-temp");
    expect(h.toast.success).toHaveBeenCalledWith("Copied to clipboard");
  });

  it("surfaces reset failures", async () => {
    h.reset.mutateAsync = vi.fn().mockRejectedValue(new Error("423 locked"));
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Reset password"));
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("423 locked"));
  });

  it("unlocks a user", async () => {
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Unlock"));
    await waitFor(() => expect(h.unlock.mutateAsync).toHaveBeenCalledWith(2));
    expect(h.toast.success).toHaveBeenCalledWith("Unlocked bob");
  });

  it("deletes a user after confirmation", async () => {
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Delete"));
    expect(screen.getByText("Delete bob?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(h.del.mutateAsync).toHaveBeenCalledWith(2));
    expect(h.toast.success).toHaveBeenCalledWith("Deleted bob");
  });

  it("cancels a delete without calling the API", async () => {
    renderPage();
    await openRowMenu(1);
    await userEvent.click(await screen.findByText("Delete"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(h.del.mutateAsync).not.toHaveBeenCalled();
  });

  it("hides Delete in the current user's own row menu", async () => {
    renderPage();
    await openRowMenu(0);
    expect(await screen.findByText("Edit")).toBeInTheDocument();
    expect(screen.queryByText("Delete")).not.toBeInTheDocument();
  });
});
