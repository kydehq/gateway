import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  rules: { data: undefined as unknown, isLoading: true },
  createSpy: vi.fn(),
  deleteSpy: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));
vi.mock("@/api/queries", () => ({
  useDlpRules: () => h.rules,
  useCreateDlpRule: () => ({ mutateAsync: h.createSpy, isPending: false }),
  useDeleteDlpRule: () => ({ mutateAsync: h.deleteSpy, isPending: false }),
}));
vi.mock("sonner", () => ({
  toast: { success: (...a: unknown[]) => h.toastSuccess(...a), error: (...a: unknown[]) => h.toastError(...a) },
}));

import DlpRulesPage from "./dlp-rules";

function renderPage() {
  return render(
    <MemoryRouter>
      <DlpRulesPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.rules = { data: undefined, isLoading: true };
  h.createSpy.mockReset().mockResolvedValue(undefined);
  h.deleteSpy.mockReset().mockResolvedValue(undefined);
  h.toastError.mockReset();
  h.toastSuccess.mockReset();
});

describe("DlpRulesPage", () => {
  it("shows the empty state when there are no rules", () => {
    h.rules = { data: [], isLoading: false };
    renderPage();
    expect(screen.getByText(/No rules yet/)).toBeInTheDocument();
  });

  it("renders a rule row from data", () => {
    h.rules = {
      data: [
        {
          id: 1, kind: "allow", scanner: "regex", entity_type: "EMAIL_ADDRESS",
          match_text: null, note: "noisy", hit_count: 7, last_hit_at: 0,
          created_by_username: "admin", created_at: 1,
        },
      ],
      isLoading: false,
    };
    renderPage();
    expect(screen.getByText("EMAIL_ADDRESS")).toBeInTheDocument();
    expect(screen.getByText("noisy")).toBeInTheDocument();
  });

  it("rejects an add with no entity type", async () => {
    h.rules = { data: [], isLoading: false };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    expect(h.toastError).toHaveBeenCalledWith("Entity type is required.");
    expect(h.createSpy).not.toHaveBeenCalled();
  });

  it("submits a valid new allowlist rule", async () => {
    h.rules = { data: [], isLoading: false };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Entity type"), "AWS_ACCESS_KEY");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await vi.waitFor(() =>
      expect(h.createSpy).toHaveBeenCalledWith({
        kind: "allow",
        scanner: "regex",
        entity_type: "AWS_ACCESS_KEY",
        match_text: null,
        note: "",
      }),
    );
  });
});
