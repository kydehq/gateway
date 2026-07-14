import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import AuditApiPage from "./audit-api";

describe("AuditApiPage", () => {
  it("renders the API reference header", () => {
    render(
      <MemoryRouter>
        <AuditApiPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Audit API")).toBeInTheDocument();
  });
});
