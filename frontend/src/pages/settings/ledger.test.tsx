import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  config: { data: undefined as unknown, isLoading: true },
}));
vi.mock("@/api/queries", () => ({ useConfiguration: () => h.config }));

import SettingsLedgerPage from "./ledger";

beforeEach(() => {
  h.config = { data: undefined, isLoading: true };
});

describe("SettingsLedgerPage", () => {
  it("renders ledger backend and entry count once loaded", () => {
    h.config = {
      data: { ledger_backend: "postgres", ledger_entry_count: 1234 },
      isLoading: false,
    };
    render(<SettingsLedgerPage />);
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument(); // toLocaleString
  });

  it("shows a skeleton while loading (no values yet)", () => {
    render(<SettingsLedgerPage />);
    expect(screen.queryByText("postgres")).not.toBeInTheDocument();
  });
});
