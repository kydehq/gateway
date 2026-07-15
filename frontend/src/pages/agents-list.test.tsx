import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Agent, FleetTrust } from "@/api/types";

const h = vi.hoisted(() => ({
  agents: { data: undefined as Agent[] | undefined, isLoading: false },
  trust: { data: undefined as FleetTrust | undefined },
}));
vi.mock("@/api/queries", () => ({
  useAgents: () => h.agents,
  useFleetTrust: () => h.trust,
}));

import AgentsListPage from "./agents-list";

function agent(overrides: Partial<Agent>): Agent {
  return {
    agent_id: "agent:aaaa1111bbbb2222cccc",
    display_name: null,
    first_seen: 100,
    last_seen: 200,
    first_seen_dt: "2026-06-01T08:00:00Z",
    last_seen_dt: "2026-06-20T09:30:00Z",
    entry_count: 10,
    session_count: 2,
    ...overrides,
  };
}

const ROSTER: Agent[] = [
  agent({
    agent_id: "agent:aaaa1111bbbb2222cccc",
    display_name: "Billing Bot",
    last_seen: 300,
    // Recent → counts as active.
    last_seen_dt: new Date().toISOString(),
    entry_count: 90,
    session_count: 9,
  }),
  agent({
    agent_id: "agent:dddd3333eeee4444ffff",
    last_seen: 200,
    entry_count: 50,
    session_count: 5,
  }),
  agent({
    agent_id: "agent:zzzz9999yyyy8888xxxx",
    last_seen: 100,
    entry_count: 10,
    session_count: 1,
    first_seen_dt: "",
    last_seen_dt: "",
  }),
];

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentsListPage />
    </MemoryRouter>,
  );
}

function agentColumn(): Array<string | null> {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].querySelector("a")!.textContent);
}

beforeEach(() => {
  h.agents = { data: ROSTER, isLoading: false };
  h.trust = {
    data: {
      trust_score: 80,
      tier: "Monitored",
      tier_key: "monitored",
      active_agents: 3,
      dimensions: { security: 80, compliance: 80, integrity: 80, reliability: 80, economics: 80 },
      tier_counts: { autonomous: 0, monitored: 3, human_approval: 0, isolated: 0 },
      signing_enabled: true,
      agents: [
        {
          agent_id: "agent:aaaa1111bbbb2222cccc",
          display_name: "Billing Bot",
          score: 91,
          tier: "Autonomous",
          tier_key: "autonomous",
          cap_reason: null,
          dimensions: { security: 90, compliance: 90, integrity: 90, reliability: 90, economics: 90 },
          requests: 90,
          last_seen: null,
        },
        {
          agent_id: "agent:dddd3333eeee4444ffff",
          display_name: null,
          score: 55,
          tier: "Monitored",
          tier_key: "monitored",
          cap_reason: null,
          dimensions: { security: 55, compliance: 55, integrity: 55, reliability: 55, economics: 55 },
          requests: 50,
          last_seen: null,
        },
      ],
    },
  };
});

describe("AgentsListPage", () => {
  it("shows skeletons while loading", () => {
    h.agents = { data: undefined, isLoading: true };
    const { container } = renderPage();
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the no-agents empty state", () => {
    h.agents = { data: [], isLoading: false };
    renderPage();
    expect(screen.getByText("No agents observed yet.")).toBeInTheDocument();
  });

  it("computes the KPI values", () => {
    renderPage();
    const kpi = (label: string) =>
      screen.getByText(label).parentElement as HTMLElement;
    expect(kpi("Total agents")).toHaveTextContent("3");
    expect(kpi("Active (last 24h)")).toHaveTextContent("1");
    expect(kpi("With display name")).toHaveTextContent("1 / 3");
  });

  it("renders rows with label, trust badge, dates, and status chips", () => {
    renderPage();
    // Default sort: last_seen desc → Billing Bot first.
    expect(agentColumn()[0]).toBe("Billing Bot");
    // Labeled agents also show the raw-id excerpt.
    expect(screen.getByText("agent:aaaa1111bbbb…")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Billing Bot" })).toHaveAttribute(
      "href",
      "/agents/agent%3Aaaaa1111bbbb2222cccc",
    );
    // Trust badge scores; the third agent has no trust row.
    expect(screen.getByText("91")).toBeInTheDocument();
    expect(screen.getByText("55")).toBeInTheDocument();
    // Missing trust row and the empty dates all render as dashes.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    // Date formatting: first_seen_dt sliced to the day, empty → "—" too.
    expect(screen.getAllByText("2026-06-01").length).toBe(2);
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getAllByText("idle").length).toBe(2);
  });

  it("filters by display name or agent_id", async () => {
    renderPage();
    const input = screen.getByPlaceholderText("Filter by name or agent_id…");
    await userEvent.type(input, "billing");
    expect(agentColumn()).toEqual(["Billing Bot"]);
    await userEvent.clear(input);
    await userEvent.type(input, "dddd3333");
    expect(agentColumn()).toEqual(["agent:dddd3333eeee4444ffff"]);
    await userEvent.clear(input);
    await userEvent.type(input, "nothing-matches");
    expect(screen.getByText("No agents match the search.")).toBeInTheDocument();
  });

  it("sorts by label asc on first click, then flips", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Agent/ }));
    // Label asc: "agent:dddd…" < "agent:zzzz…" < "billing bot".
    expect(agentColumn()).toEqual([
      "agent:dddd3333eeee4444ffff",
      "agent:zzzz9999yyyy8888xxxx",
      "Billing Bot",
    ]);
    await userEvent.click(screen.getByRole("button", { name: /Agent/ }));
    expect(agentColumn()[0]).toBe("Billing Bot");
  });

  it("sorts numeric columns desc by default", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Trust/ }));
    // Trust desc: 91, 55, missing (-1).
    expect(agentColumn()).toEqual([
      "Billing Bot",
      "agent:dddd3333eeee4444ffff",
      "agent:zzzz9999yyyy8888xxxx",
    ]);
    await userEvent.click(screen.getByRole("button", { name: /Sessions/ }));
    expect(agentColumn()[0]).toBe("Billing Bot");
  });
});
