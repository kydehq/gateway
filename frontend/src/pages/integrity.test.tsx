import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mutable holders so each test can set the hook return before rendering.
const h = vi.hoisted(() => ({
  verify: {
    data: undefined as unknown,
    isLoading: false,
    isError: false,
    error: undefined as unknown,
    dataUpdatedAt: 0,
  },
  features: { signingEnabled: true },
}));

vi.mock("@/api/queries", () => ({ useVerify: () => h.verify }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));

import IntegrityPage from "./integrity";

function renderPage() {
  return render(
    <MemoryRouter>
      <IntegrityPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.verify = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: undefined,
    dataUpdatedAt: 0,
  };
  h.features = { signingEnabled: true };
});

describe("IntegrityPage", () => {
  it("shows the upgrade notice when signing is disabled (starter)", () => {
    h.features = { signingEnabled: false };
    renderPage();
    expect(screen.getByText(/Verifiable audit ledger/)).toBeInTheDocument();
    // No metric cards in the locked state.
    expect(screen.queryByText("Entries Verified")).not.toBeInTheDocument();
  });

  it("renders metric cards and the all-clear when the chain is valid", () => {
    h.verify = {
      data: {
        entry_count: 42,
        chain_breaks: 0,
        signature_failures: 0,
        valid: true,
        fingerprint: "ab12cd34",
        errors: [],
      },
      isLoading: false,
      isError: false,
      error: undefined,
      dataUpdatedAt: 1,
    };
    renderPage();
    expect(screen.getByText("Entries Verified")).toBeInTheDocument();
    expect(screen.getByText("ab12cd34")).toBeInTheDocument();
    expect(screen.getByText("All checks passed.")).toBeInTheDocument();
  });

  it("lists errors when the ledger has integrity breaks", () => {
    h.verify = {
      data: {
        entry_count: 5,
        chain_breaks: 1,
        signature_failures: 2,
        valid: false,
        fingerprint: "",
        errors: ["Chain break at seq 3", "Invalid signature at seq 4"],
      },
      isLoading: false,
      isError: false,
      error: undefined,
      dataUpdatedAt: 1,
    };
    renderPage();
    expect(screen.getByText(/Errors \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Chain break at seq 3")).toBeInTheDocument();
  });

  it("surfaces an error message when the query fails", () => {
    h.verify = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      dataUpdatedAt: 0,
    };
    renderPage();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
