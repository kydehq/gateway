import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Policy, SettingEntry } from "@/api/types";

const h = vi.hoisted(() => ({
  policies: {
    data: undefined as Policy[] | undefined,
    isLoading: false,
    isError: false,
    error: null as Error | null,
    dataUpdatedAt: 1,
  },
  settings: {
    data: undefined as SettingEntry[] | undefined,
    isLoading: false,
    isError: false,
  },
  toggle: { mutateAsync: vi.fn(), isPending: false },
  bulk: { mutateAsync: vi.fn(), isPending: false },
  resync: { mutateAsync: vi.fn(), isPending: false },
  updateSetting: { mutateAsync: vi.fn(), isPending: false },
  me: { isAdmin: true },
  features: { enforcementEnabled: true },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  usePolicies: () => h.policies,
  useSettings: () => h.settings,
  useTogglePolicy: () => h.toggle,
  usePreventionBulk: () => h.bulk,
  useResyncPolicies: () => h.resync,
  useUpdateSetting: () => h.updateSetting,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("sonner", () => ({ toast: h.toast }));

import PoliciesPage from "./policies";

function makePolicy(overrides: Partial<Policy>): Policy {
  return {
    id: "email-1",
    name: "Email address",
    source: "builtin",
    category: "pii",
    severity: "medium",
    pattern: "[a-z]+@[a-z]+",
    description: "",
    enabled: true,
    prevention: false,
    hits: 10,
    last_hit_at: null,
    ...overrides,
  };
}

function setting(key: string, value: string | boolean): SettingEntry {
  return {
    key,
    label: key,
    description: "",
    type: "bool",
    default: false,
    value,
    source: "db",
    updated_at: null,
    updated_by: null,
    updated_by_username: null,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PoliciesPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.policies = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    dataUpdatedAt: 1,
  };
  h.settings = { data: [], isLoading: false, isError: false };
  h.toggle = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.bulk = {
    mutateAsync: vi.fn().mockResolvedValue({ updated: 5 }),
    isPending: false,
  };
  h.resync = {
    mutateAsync: vi.fn().mockResolvedValue({ loaded: 42 }),
    isPending: false,
  };
  h.updateSetting = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.me = { isAdmin: true };
  h.features = { enforcementEnabled: true };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("PoliciesPage — table states", () => {
  it("shows skeleton rows while loading", () => {
    h.policies.isLoading = true;
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the error row when the query fails", () => {
    h.policies = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("500 upstream"),
      dataUpdatedAt: 0,
    };
    renderPage();
    expect(screen.getByText("Failed to load: 500 upstream")).toBeInTheDocument();
  });

  it("shows the empty state when no patterns are bundled", () => {
    h.policies.data = [];
    renderPage();
    expect(screen.getByText(/No bundled patterns/)).toBeInTheDocument();
  });

  it("groups patterns by source with disabled/preventing counts", () => {
    h.policies.data = [
      makePolicy({ id: "a", source: "builtin", enabled: false }),
      makePolicy({ id: "b", source: "builtin", prevention: true }),
      makePolicy({ id: "c", source: "custom", name: "IBAN", severity: "high" }),
    ];
    renderPage();
    expect(screen.getByText(/builtin · 2 patterns/)).toBeInTheDocument();
    expect(screen.getByText("· 1 disabled")).toBeInTheDocument();
    expect(screen.getByText("· 1 preventing")).toBeInTheDocument();
    expect(screen.getByText(/custom · 1 pattern/)).toBeInTheDocument();
    expect(screen.getByText("IBAN")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    // No last hit → "never".
    expect(screen.getAllByText("never").length).toBe(3);
  });
});

describe("PoliciesPage — per-pattern toggles", () => {
  it("disables a policy and reports it", async () => {
    h.policies.data = [makePolicy({})];
    renderPage();
    await userEvent.click(screen.getByRole("switch", { name: "Disable policy" }));
    await waitFor(() =>
      expect(h.toggle.mutateAsync).toHaveBeenCalledWith({
        id: "email-1",
        enabled: false,
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Disabled Email address"),
    );
  });

  it("re-enables a disabled policy", async () => {
    h.policies.data = [makePolicy({ enabled: false })];
    renderPage();
    await userEvent.click(screen.getByRole("switch", { name: "Enable policy" }));
    await waitFor(() =>
      expect(h.toggle.mutateAsync).toHaveBeenCalledWith({
        id: "email-1",
        enabled: true,
      }),
    );
  });

  it("opts a pattern into prevention", async () => {
    h.policies.data = [makePolicy({})];
    renderPage();
    await userEvent.click(
      screen.getByRole("switch", { name: "Enable prevention for this policy" }),
    );
    await waitFor(() =>
      expect(h.toggle.mutateAsync).toHaveBeenCalledWith({
        id: "email-1",
        prevention: true,
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(
      expect.stringContaining("BLOCKS requests"),
    );
  });

  it("surfaces toggle failures as an error toast", async () => {
    h.policies.data = [makePolicy({})];
    h.toggle.mutateAsync = vi.fn().mockRejectedValue(new Error("403 forbidden"));
    renderPage();
    await userEvent.click(screen.getByRole("switch", { name: "Disable policy" }));
    await waitFor(() =>
      expect(h.toast.error).toHaveBeenCalledWith("403 forbidden"),
    );
  });

  it("renders the read-only badge and inert switches for non-admins", () => {
    h.me = { isAdmin: false };
    h.policies.data = [makePolicy({})];
    renderPage();
    expect(screen.getByText(/Read-only — changing policies/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Disable policy" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Re-sync to dlp-regex/ }),
    ).toBeDisabled();
  });
});

describe("PoliciesPage — prevention card & bulk actions", () => {
  it("reads the master-switch states from settings", () => {
    h.settings.data = [
      setting("DLP_REGEX_PREVENTION", "true"),
      setting("DLP_BERT_PREVENTION", false),
    ];
    h.policies.data = [];
    renderPage();
    const regexSwitch = screen.getByRole("switch", {
      name: "Toggle Policy Prevention",
    });
    const bertSwitch = screen.getByRole("switch", {
      name: "Toggle BERT Prevention",
    });
    expect(regexSwitch).toHaveAttribute("aria-checked", "true");
    expect(bertSwitch).toHaveAttribute("aria-checked", "false");
  });

  it("flips a master switch through the settings mutation", async () => {
    h.settings.data = [setting("DLP_REGEX_PREVENTION", false)];
    h.policies.data = [];
    renderPage();
    await userEvent.click(
      screen.getByRole("switch", { name: "Toggle Policy Prevention" }),
    );
    await waitFor(() =>
      expect(h.updateSetting.mutateAsync).toHaveBeenCalledWith({
        key: "DLP_REGEX_PREVENTION",
        value: "true",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Policy Prevention is ACTIVE"),
    );
  });

  it("bulk-enables prevention across all patterns", async () => {
    h.policies.data = [];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Enable all" }));
    await waitFor(() => expect(h.bulk.mutateAsync).toHaveBeenCalledWith(true));
    expect(h.toast.success).toHaveBeenCalledWith(
      "Prevention enabled for 5 patterns",
    );
    await userEvent.click(screen.getByRole("button", { name: "Disable all" }));
    await waitFor(() => expect(h.bulk.mutateAsync).toHaveBeenCalledWith(false));
  });

  it("re-syncs patterns to the dlp-regex sidecar", async () => {
    h.policies.data = [];
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: /Re-sync to dlp-regex/ }),
    );
    await waitFor(() => expect(h.resync.mutateAsync).toHaveBeenCalled());
    expect(h.toast.success).toHaveBeenCalledWith(
      "Pushed 42 patterns to dlp-regex",
    );
  });

  it("shows the sandbox note when enforcement is unavailable", () => {
    h.features = { enforcementEnabled: false };
    h.policies.data = [];
    renderPage();
    expect(
      screen.getByText(/Detection and alerts run in the sandbox edition/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Enterprise").length).toBeGreaterThan(0);
  });
});

describe("PoliciesPage — sorting", () => {
  it("sorts within a group when a header is clicked", async () => {
    h.policies.data = [
      makePolicy({ id: "a", name: "Alpha", hits: 5 }),
      makePolicy({ id: "b", name: "Beta", hits: 50 }),
    ];
    renderPage();
    // Default sort is hits desc → Beta first.
    let rows = screen.getAllByRole("row");
    let names = rows.map((r) => within(r).queryByText(/Alpha|Beta/)?.textContent);
    expect(names.filter(Boolean)).toEqual(["Beta", "Alpha"]);
    // Sort by name asc.
    await userEvent.click(screen.getByText("Name"));
    rows = screen.getAllByRole("row");
    names = rows.map((r) => within(r).queryByText(/Alpha|Beta/)?.textContent);
    expect(names.filter(Boolean)).toEqual(["Alpha", "Beta"]);
  });
});
