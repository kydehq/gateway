import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { createSpy, updateSpy } = vi.hoisted(() => ({
  createSpy: vi.fn(),
  updateSpy: vi.fn(),
}));
vi.mock("@/api/queries", () => ({
  useCreateUser: () => ({ mutateAsync: createSpy, isPending: false }),
  useUpdateUser: () => ({ mutateAsync: updateSpy, isPending: false }),
}));

import { UsersDialog } from "./users-dialog";

beforeEach(() => {
  createSpy.mockReset().mockResolvedValue(undefined);
  updateSpy.mockReset().mockResolvedValue(undefined);
});

describe("UsersDialog — add validation", () => {
  it("blocks submit until the required fields are filled", async () => {
    const user = userEvent.setup();
    render(<UsersDialog open onOpenChange={vi.fn()} />);

    // Username + password are empty; roles default to ["viewer"].
    await user.click(screen.getByRole("button", { name: "Create" }));

    const required = await screen.findAllByText("Required");
    expect(required.length).toBeGreaterThanOrEqual(2); // username + password
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("rejects a malformed email via zod (form is noValidate, so zod is authoritative)", async () => {
    const user = userEvent.setup();
    render(<UsersDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("Invalid email")).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("requires at least one role", async () => {
    const user = userEvent.setup();
    render(<UsersDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "pw");
    // Default role "viewer" is pre-checked — clear it to leave roles empty.
    await user.click(screen.getByText("viewer"));
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText(/Pick at least one role/),
    ).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("submits valid input (empty email is allowed)", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<UsersDialog open onOpenChange={onOpenChange} />);

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await vi.waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith({
        username: "bob",
        email: "",
        password: "secret",
        roles: ["viewer"],
      }),
    );
  });
});
