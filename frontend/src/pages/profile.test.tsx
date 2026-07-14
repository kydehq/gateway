import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const { changeSpy, emailSpy } = vi.hoisted(() => ({
  changeSpy: vi.fn(),
  emailSpy: vi.fn(),
}));
// ProfilePage pulls useMe (the query), useUpdateEmail, and useChangePassword
// from @/api/queries; stub all three.
vi.mock("@/api/queries", () => ({
  useMe: () => ({ data: { email: "user@example.com", roles: ["admin"] }, isLoading: false }),
  useUpdateEmail: () => ({ mutateAsync: emailSpy, isPending: false }),
  useChangePassword: () => ({ mutateAsync: changeSpy, isPending: false }),
}));
// Avoid sonner's portal/timer machinery in the assertion path.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import ProfilePage from "./profile";

function renderProfile() {
  return render(
    <MemoryRouter>
      <ProfilePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  changeSpy.mockReset().mockResolvedValue(undefined);
  emailSpy.mockReset().mockResolvedValue(undefined);
});

describe("ProfilePage — password change validation", () => {
  // Labels resolve via getByLabelText only because PasswordInput forwards the
  // FormControl-injected id to its inner <input>; this also guards that fix.
  it("enforces the 8-character minimum on the new password", async () => {
    const user = userEvent.setup();
    renderProfile();

    await user.type(screen.getByLabelText("New password"), "short");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/Minimum 8 characters/)).toBeInTheDocument();
    expect(changeSpy).not.toHaveBeenCalled();
  });

  it("rejects a confirmation that does not match", async () => {
    const user = userEvent.setup();
    renderProfile();

    await user.type(screen.getByLabelText("Current password"), "oldpass1");
    await user.type(screen.getByLabelText("New password"), "longenough8");
    await user.type(screen.getByLabelText("Confirm new password"), "different8");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/Passwords don't match/)).toBeInTheDocument();
    expect(changeSpy).not.toHaveBeenCalled();
  });

  it("submits a valid matching password change (current + new only)", async () => {
    const user = userEvent.setup();
    renderProfile();

    await user.type(screen.getByLabelText("Current password"), "oldpass1");
    await user.type(screen.getByLabelText("New password"), "longenough8");
    await user.type(screen.getByLabelText("Confirm new password"), "longenough8");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await vi.waitFor(() =>
      expect(changeSpy).toHaveBeenCalledWith({
        current_password: "oldpass1",
        new_password: "longenough8",
      }),
    );
  });
});
