import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// AgentEndpoints fetches config; stub it so this stays a page-shell test.
vi.mock("@/components/shared/agent-endpoints", () => ({
  AgentEndpoints: () => <div data-testid="agent-endpoints" />,
}));

import LlmRoutingPage from "./llm-routing";

describe("LlmRoutingPage", () => {
  it("renders the providers header, config note, and endpoints block", () => {
    render(
      <MemoryRouter>
        <LlmRoutingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("LLM Providers")).toBeInTheDocument();
    expect(screen.getByText(/config\.yaml/)).toBeInTheDocument();
    expect(screen.getByTestId("agent-endpoints")).toBeInTheDocument();
  });
});
