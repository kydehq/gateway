import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/queries", () => ({
  useServiceMetrics: () => ({ dataUpdatedAt: 0 }),
}));

import SettingsLayout from "./settings";

describe("SettingsLayout", () => {
  it("renders the settings header and left-rail navigation", () => {
    render(
      <MemoryRouter>
        <SettingsLayout />
      </MemoryRouter>,
    );
    expect(screen.getByText("Settings")).toBeInTheDocument();
    for (const label of ["Overview", "Runtime tuning", "Email", "Signing", "Ledger"]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });
});
