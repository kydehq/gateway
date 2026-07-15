import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

import { CopyButton } from "./copy-button";

const writeText = vi.fn();

beforeEach(() => {
  writeText.mockReset();
  toast.success.mockReset();
  // jsdom has no clipboard; install a minimal one.
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

describe("CopyButton", () => {
  it("copies the value and toasts with the label", async () => {
    render(<CopyButton value="abc123" label="entry id" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy entry id" }));
    expect(writeText).toHaveBeenCalledWith("abc123");
    expect(toast.success).toHaveBeenCalledWith("Copied entry id");
  });

  it("uses the generic label when none is given", async () => {
    render(<CopyButton value="xyz" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("xyz");
    expect(toast.success).toHaveBeenCalledWith("Copied");
  });
});
