import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import RoutingLayout from "./routing";

describe("RoutingLayout", () => {
  it("renders the routing left-rail links", () => {
    render(
      <MemoryRouter>
        <RoutingLayout />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: /LLM Providers/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /MCP Servers/ })).toBeInTheDocument();
  });
});
