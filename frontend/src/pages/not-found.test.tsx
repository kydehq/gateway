import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import NotFoundPage from "./not-found";

describe("NotFoundPage", () => {
  it("renders the not-found header and a link back to overview", () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Not found")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Back to Overview" });
    expect(link).toHaveAttribute("href", "/");
  });
});
