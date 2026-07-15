import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { DlpRule } from "@/api/types";

const h = vi.hoisted(() => ({
  rules: { data: undefined as DlpRule[] | undefined, isLoading: false },
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

let ruleSeq = 0;
function rule(overrides: Partial<DlpRule>): DlpRule {
  ruleSeq += 1;
  return {
    id: ruleSeq,
    kind: "allow",
    scanner: "regex",
    entity_type: "EMAIL_ADDRESS",
    match_text: null,
    note: "noisy",
    hit_count: 7,
    last_hit_at: 0,
    created_by: 1,
    created_by_username: "admin",
    created_at: 1750000000,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <DlpRulesPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  ruleSeq = 0;
  h.rules = { data: [], isLoading: false };
  h.createSpy.mockReset().mockResolvedValue(undefined);
  h.deleteSpy.mockReset().mockResolvedValue(undefined);
  h.toastError.mockReset();
  h.toastSuccess.mockReset();
});

describe("DlpRulesPage — table", () => {
  it("shows a skeleton while loading", () => {
    h.rules = { data: undefined, isLoading: true };
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state when there are no rules", () => {
    renderPage();
    expect(screen.getByText(/No rules yet/)).toBeInTheDocument();
  });

  it("renders rule rows with scope, scanner, and fallbacks", () => {
    h.rules = {
      data: [
        rule({}),
        rule({
          scanner: null,
          entity_type: "PII",
          match_text: "test@example.com",
          note: "",
          created_by_username: null,
          last_hit_at: 1750000000,
          hit_count: 3,
        }),
      ],
      isLoading: false,
    };
    renderPage();
    expect(screen.getByText("EMAIL_ADDRESS")).toBeInTheDocument();
    expect(screen.getByText("noisy")).toBeInTheDocument();
    // Entity-type-wide rule vs exact-match rule.
    expect(screen.getByText("entity type")).toBeInTheDocument();
    expect(screen.getByText("(any)")).toBeInTheDocument();
    expect(screen.getByText("exact match")).toBeInTheDocument();
    expect(screen.getByText("test@example.com")).toBeInTheDocument();
    // NULL scanner renders as "any"; empty note and creator as dashes.
    expect(screen.getByText("any")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getAllByText("allow").length).toBe(2);
  });
});

describe("DlpRulesPage — add rule", () => {
  it("rejects an add with no entity type", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    expect(h.toastError).toHaveBeenCalledWith("Entity type is required.");
    expect(h.createSpy).not.toHaveBeenCalled();
  });

  it("submits a valid new allowlist rule", async () => {
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
    expect(h.toastSuccess).toHaveBeenCalledWith("Rule added.");
  });

  it("includes match text and note when provided", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Entity type"), "EMAIL_ADDRESS");
    await user.type(
      within(dialog).getByLabelText("Match text (optional)"),
      "test@example.com",
    );
    await user.type(within(dialog).getByLabelText("Note (optional)"), "fixture data");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await vi.waitFor(() =>
      expect(h.createSpy).toHaveBeenCalledWith({
        kind: "allow",
        scanner: "regex",
        entity_type: "EMAIL_ADDRESS",
        match_text: "test@example.com",
        note: "fixture data",
      }),
    );
  });

  it("surfaces create failures as an error toast", async () => {
    h.createSpy.mockRejectedValue(new Error("409 duplicate"));
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Add rule/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Entity type"), "PII");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await vi.waitFor(() => expect(h.toastError).toHaveBeenCalledWith("409 duplicate"));
  });
});

describe("DlpRulesPage — delete rule", () => {
  it("removes a rule after confirmation", async () => {
    h.rules = { data: [rule({ id: 42 })], isLoading: false };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Remove rule" }));
    expect(await screen.findByText("Remove this rule?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await vi.waitFor(() => expect(h.deleteSpy).toHaveBeenCalledWith(42));
    expect(h.toastSuccess).toHaveBeenCalledWith("Rule removed.");
  });

  it("cancels a delete without calling the API", async () => {
    h.rules = { data: [rule({})], isLoading: false };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Remove rule" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(h.deleteSpy).not.toHaveBeenCalled();
  });

  it("surfaces delete failures as an error toast", async () => {
    h.rules = { data: [rule({})], isLoading: false };
    h.deleteSpy.mockRejectedValue(new Error("410 gone"));
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Remove rule" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await vi.waitFor(() => expect(h.toastError).toHaveBeenCalledWith("410 gone"));
  });
});
