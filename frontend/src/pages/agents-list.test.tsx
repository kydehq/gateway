import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  agents: { data: undefined as unknown, isLoading: true },
  trust: { data: undefined as unknown },
}));
vi.mock("@/api/queries", () => ({
  useAgents: () => h.agents,
  useFleetTrust: () => h.trust,
}));
vi.mock("@/components/shared/trust-score", () => ({
  TrustBadge: () => <span>trust</span>,
}));

import AgentsListPage from "./agents-list";

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.agents = { data: undefined, isLoading: true };
  h.trust = { data: undefined };
});

describe("AgentsListPage", () => {
  it("renders the header", () => {
    renderPage();
    expect(screen.getByText("Agents")).toBeInTheDocument();
  });

  it("shows the empty state when no agents are observed", () => {
    h.agents = { data: [], isLoading: false };
    renderPage();
    expect(screen.getByText("No agents observed yet.")).toBeInTheDocument();
  });

  it("renders an agent row using its display name", () => {
    h.agents = {
      data: [
        {
          agent_id: "agent:abc",
          display_name: "Billing Bot",
          last_seen_dt: null,
          first_seen_dt: null,
          session_count: 3,
          entry_count: 9,
        },
      ],
      isLoading: false,
    };
    renderPage();
    expect(screen.getByText("Billing Bot")).toBeInTheDocument();
  });
});
