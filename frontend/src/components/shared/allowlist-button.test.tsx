import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const h = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  isPending: false,
}));
vi.mock("@/api/queries", () => ({
  useCreateDlpRule: () => ({ mutateAsync: h.mutateAsync, isPending: h.isPending }),
}));

import { AllowlistButton } from "./allowlist-button";

beforeEach(() => {
  h.mutateAsync = vi.fn().mockResolvedValue({});
  h.isPending = false;
  toast.success.mockReset();
  toast.error.mockReset();
});

async function openDialog() {
  await userEvent.click(screen.getByRole("button", { name: /Allowlist/ }));
}

describe("AllowlistButton", () => {
  it("submits an exact-match rule when matched text is available", async () => {
    render(
      <AllowlistButton
        scanner="regex"
        entityType="EMAIL_ADDRESS"
        matchText="kim@example.com"
      />,
    );
    await openDialog();
    expect(screen.getByText("Add allowlist rule")).toBeInTheDocument();
    expect(screen.getByText("kim@example.com")).toBeInTheDocument();

    await userEvent.type(
      screen.getByLabelText("Note (optional)"),
      "  internal system email  ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));

    expect(h.mutateAsync).toHaveBeenCalledWith({
      kind: "allow",
      scanner: "regex",
      entity_type: "EMAIL_ADDRESS",
      match_text: "kim@example.com",
      note: "internal system email",
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Allowlisted EMAIL_ADDRESS: kim@example.com",
    );
    // Dialog closes on success.
    expect(screen.queryByText("Add allowlist rule")).not.toBeInTheDocument();
  });

  it("defaults to a type-wide rule when there is no matched text", async () => {
    render(<AllowlistButton scanner="bert" entityType="IBAN" />);
    await openDialog();
    expect(
      screen.getByText(/Every future IBAN finding from bert will be suppressed/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));

    expect(h.mutateAsync).toHaveBeenCalledWith({
      kind: "allow",
      scanner: "bert",
      entity_type: "IBAN",
      match_text: null,
      note: "",
    });
    expect(toast.success).toHaveBeenCalledWith("Allowlisted every IBAN match");
  });

  it("surfaces API errors as a toast and keeps the dialog open", async () => {
    h.mutateAsync = vi.fn().mockRejectedValue(new Error("403 forbidden"));
    render(<AllowlistButton scanner="regex" entityType="IBAN" />);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));

    expect(toast.error).toHaveBeenCalledWith("403 forbidden");
    expect(screen.getByText("Add allowlist rule")).toBeInTheDocument();
  });

  it("can be dismissed with Cancel", async () => {
    render(<AllowlistButton scanner="regex" entityType="IBAN" compact />);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Add allowlist rule")).not.toBeInTheDocument();
    expect(h.mutateAsync).not.toHaveBeenCalled();
  });

  it("disables submission while the mutation is pending", async () => {
    h.isPending = true;
    render(<AllowlistButton scanner="regex" entityType="IBAN" />);
    await openDialog();
    expect(screen.getByRole("button", { name: "Adding…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
});
