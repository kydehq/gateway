import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "@/api/types";

const { createSpy, updateSpy } = vi.hoisted(() => ({
  createSpy: vi.fn(),
  updateSpy: vi.fn(),
}));
vi.mock("@/api/queries", () => ({
  useCreateUser: () => ({ mutateAsync: createSpy, isPending: false }),
  useUpdateUser: () => ({ mutateAsync: updateSpy, isPending: false }),
}));

import { UsersDialog } from "./users-dialog";

const BOB: User = {
  id: 2,
  username: "bob",
  email: "bob@x.test",
  roles: ["auditor"],
};

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

  it("shows the temp-password panel when the backend returns one", async () => {
    createSpy.mockResolvedValue({ temp_password: "tmp-123" });
    const user = userEvent.setup();
    // userEvent.setup() installs its own clipboard stub — spy on that
    // instead of replacing navigator.clipboard beforehand.
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    const onOpenChange = vi.fn();
    render(<UsersDialog open onOpenChange={onOpenChange} />);

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("Temporary password")).toBeInTheDocument();
    expect(screen.getByText("tmp-123")).toBeInTheDocument();
    // The dialog stays open so the admin can copy the secret.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("tmp-123");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("renders the backend error as a root message", async () => {
    createSpy.mockRejectedValue(new Error("username taken"));
    const user = userEvent.setup();
    render(<UsersDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("username taken")).toBeInTheDocument();
  });
});

describe("UsersDialog — edit", () => {
  it("shows the immutable username and prefilled email/roles", () => {
    render(<UsersDialog open onOpenChange={vi.fn()} user={BOB} />);
    expect(screen.getByText("Edit user")).toBeInTheDocument();
    expect(screen.getByDisplayValue("bob")).toBeDisabled();
    expect(screen.getByLabelText("Email")).toHaveValue("bob@x.test");
    // No password field on edit.
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("updates email and roles through the mutation", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<UsersDialog open onOpenChange={onOpenChange} user={BOB} />);

    const email = screen.getByLabelText("Email");
    await user.clear(email);
    await user.type(email, "new@x.test");
    // Grant admin on top of the existing auditor role.
    await user.click(screen.getByText("admin"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await vi.waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({
        id: 2,
        email: "new@x.test",
        roles: ["auditor", "admin"],
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("requires at least one role on edit and surfaces backend failures", async () => {
    const user = userEvent.setup();
    render(<UsersDialog open onOpenChange={vi.fn()} user={BOB} />);

    await user.click(screen.getByText("auditor"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Pick at least one role/)).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();

    updateSpy.mockRejectedValue(new Error("403 forbidden"));
    await user.click(screen.getByText("auditor"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("403 forbidden")).toBeInTheDocument();
  });

  it("closes without saving via Cancel", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<UsersDialog open onOpenChange={onOpenChange} user={BOB} />);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(updateSpy).not.toHaveBeenCalled();
  });
});
