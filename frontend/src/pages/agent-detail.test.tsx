import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Agent, FleetTrust, TokenAnalysis, TopologyAgent } from "@/api/types";

const h = vi.hoisted(() => ({
  topology: {
    data: undefined as TopologyAgent | undefined,
    isLoading: false,
    isError: false,
    error: undefined as Error | undefined,
  },
  roster: { data: [] as Agent[] },
  tokens: { data: undefined as TokenAnalysis | undefined, isLoading: false },
  trust: { data: undefined as FleetTrust | undefined },
  block: { mutateAsync: vi.fn(), isPending: false },
  unblock: { mutateAsync: vi.fn(), isPending: false },
  update: { mutateAsync: vi.fn(), isPending: false },
  me: { isAdmin: true, me: { username: "admin-user" } },
  features: { enforcementEnabled: true },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useTopologyAgent: () => h.topology,
  useAgents: () => h.roster,
  useTokenAnalysis: () => h.tokens,
  useFleetTrust: () => h.trust,
  useBlockAgent: () => h.block,
  useUnblockAgent: () => h.unblock,
  useUpdateAgent: () => h.update,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("sonner", () => ({ toast: h.toast }));
// TrafficInventory has its own hooks and its own test file; stub it with a
// marker so this page test can assert the props wiring only.
vi.mock("@/components/shared/traffic-inventory", () => ({
  TrafficInventory: ({ agentId, isAdmin }: { agentId: string; isAdmin: boolean }) => (
    <div>{`traffic:${agentId}:${isAdmin}`}</div>
  ),
}));

import AgentDetailPage from "./agent-detail";

const AGENT_ID = "agent:aaaa1111bbbb2222";

const topology: TopologyAgent = {
  agent_id: AGENT_ID,
  window: "30d",
  request_count: 120,
  first_seen: 1,
  first_seen_iso: "2026-06-01T08:00:00Z",
  last_seen: 2,
  last_seen_iso: new Date().toISOString(),
  segments: [{ subnet: "10.0.0.0/24", class: "rfc1918", request_count: 100 }],
  ips: [
    { ip: "10.0.0.5", request_count: 80, ua_tool: "cursor", hostname: "build-01" },
    { ip: "10.0.0.9", request_count: 40, ua_tool: "cursor" },
  ],
  tools: [{ tool: "cursor", request_count: 100 }],
  upstreams: [{ upstream: "api.openai.com", request_count: 120 }],
  models: [{ model: "gpt-x", request_count: 120 }],
  sessions: [
    {
      session_id: "sess-1",
      request_count: 12,
      last_seen: 2,
      last_seen_iso: "2026-07-01T09:30:00Z",
      model: "gpt-x",
    },
  ],
};

const tokens = {
  total_tokens: 1700,
  total_prompt_tokens: 1500,
  total_completion_tokens: 200,
  by_hour: {},
  by_agent: {},
  by_upstream: {},
  by_model: { "gpt-x": { prompt_tokens: 1500, completion_tokens: 200 } },
} as unknown as TokenAnalysis;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/agents/${encodeURIComponent(AGENT_ID)}`]}>
      <Routes>
        <Route path="/agents/:agentId" element={<AgentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.topology = { data: topology, isLoading: false, isError: false, error: undefined };
  h.roster = {
    data: [
      {
        agent_id: AGENT_ID,
        display_name: "Deploy Bot",
        first_seen: 1,
        last_seen: 2,
        first_seen_dt: "2026-06-01T08:00:00Z",
        last_seen_dt: "2026-07-01T09:00:00Z",
        entry_count: 120,
        session_count: 4,
      },
    ],
  };
  h.tokens = { data: tokens, isLoading: false };
  h.trust = { data: undefined };
  h.block = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.unblock = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.update = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.me = { isAdmin: true, me: { username: "admin-user" } };
  h.features = { enforcementEnabled: true };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("AgentDetailPage — states", () => {
  it("shows skeletons while loading", () => {
    h.topology = { data: undefined, isLoading: true, isError: false, error: undefined };
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the error state with a back link", () => {
    h.topology = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("404 unknown agent"),
    };
    renderPage();
    expect(screen.getByText("Failed to load agent.")).toBeInTheDocument();
    expect(screen.getByText("404 unknown agent")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "← Back to Agent Activity" }),
    ).toHaveAttribute("href", "/agent-activity");
  });
});

describe("AgentDetailPage — header and KPIs", () => {
  it("renders the display name, raw id, and active chip", () => {
    renderPage();
    expect(screen.getByText("Deploy Bot")).toBeInTheDocument();
    expect(screen.getByText(AGENT_ID)).toBeInTheDocument();
    expect(screen.getByText("active (last 24h)")).toBeInTheDocument();
  });

  it("falls back to the hash-derived name and idle chip", () => {
    h.roster = { data: [] };
    h.topology = {
      ...h.topology,
      data: { ...topology, last_seen_iso: "2026-06-01T08:00:00Z" },
    };
    renderPage();
    expect(screen.getByText("Claude Code Agent (aaaa1111)")).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("renders the KPI strip from topology, roster, and token data", () => {
    renderPage();
    const kpi = (label: string) => screen.getByText(label).parentElement as HTMLElement;
    expect(kpi("Requests (30d)")).toHaveTextContent("120");
    expect(kpi("Sessions")).toHaveTextContent("4");
    expect(kpi("Tokens (30d)")).toHaveTextContent("1.7K");
    expect(kpi("Prompt / Completion")).toHaveTextContent("1.5K / 200");
  });

  it("shows the per-agent trust hero when a trust row exists", () => {
    h.trust = {
      data: {
        trust_score: 80,
        tier: "Monitored",
        tier_key: "monitored",
        active_agents: 1,
        dimensions: { security: 80, compliance: 80, integrity: 80, reliability: 80, economics: 80 },
        tier_counts: { autonomous: 0, monitored: 1, human_approval: 0, isolated: 0 },
        signing_enabled: true,
        agents: [
          {
            agent_id: AGENT_ID,
            display_name: "Deploy Bot",
            score: 76,
            tier: "Monitored",
            tier_key: "monitored",
            cap_reason: null,
            dimensions: { security: 76, compliance: 76, integrity: 76, reliability: 76, economics: 76 },
            requests: 120,
            last_seen: null,
          },
        ],
      },
    };
    renderPage();
    expect(screen.getByText("Agent Trust Score")).toBeInTheDocument();
    // The score also appears in every dimension scale.
    expect(screen.getAllByText("76").length).toBeGreaterThan(0);
    expect(screen.getByText("120 requests (30d)")).toBeInTheDocument();
  });
});

describe("AgentDetailPage — breakdowns and tables", () => {
  it("renders tools/providers/models breakdown cards", () => {
    renderPage();
    expect(screen.getByText("Tools")).toBeInTheDocument();
    expect(screen.getByText("cursor")).toBeInTheDocument();
    expect(screen.getByText("api.openai.com")).toBeInTheDocument();
    // "gpt-x" appears in Models used and the tokens-by-model table.
    expect(screen.getAllByText("gpt-x").length).toBeGreaterThan(1);
  });

  it("shows 'None.' for an empty breakdown", () => {
    h.topology = { ...h.topology, data: { ...topology, tools: [] } };
    renderPage();
    expect(screen.getByText("None.")).toBeInTheDocument();
  });

  it("renders the tokens-by-model table", () => {
    renderPage();
    expect(screen.getByText("Tokens by model (30d)")).toBeInTheDocument();
    // Prompt cell (the KPI strip renders "1.5K / 200" as one string) and
    // the total, which also appears in the Tokens KPI.
    expect(screen.getByText("1.5K")).toBeInTheDocument();
    expect(screen.getAllByText("1.7K").length).toBe(2);
  });

  it("hides the token table when there are no tokens", () => {
    h.tokens = {
      data: { ...tokens, total_tokens: 0 } as TokenAnalysis,
      isLoading: false,
    };
    renderPage();
    expect(screen.queryByText("Tokens by model (30d)")).not.toBeInTheDocument();
  });

  it("renders segments and host links with hostname formatting", () => {
    renderPage();
    expect(screen.getByText("Segments observed")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.0/24")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "build-01 (10.0.0.5)" })).toHaveAttribute(
      "href",
      "/hosts/10.0.0.5",
    );
    expect(screen.getByRole("link", { name: "10.0.0.9" })).toBeInTheDocument();
  });

  it("renders recent sessions with links, or the empty note", () => {
    renderPage();
    expect(screen.getByRole("link", { name: "sess-1" })).toHaveAttribute(
      "href",
      "/sessions/sess-1",
    );
    expect(screen.getByText("2026-07-01 09:30:00")).toBeInTheDocument();
  });

  it("shows the no-sessions note", () => {
    h.topology = { ...h.topology, data: { ...topology, sessions: [] } };
    renderPage();
    expect(screen.getByText("No sessions in the last 30 days.")).toBeInTheDocument();
  });

  it("wires TrafficInventory with the agent id and admin flag", () => {
    renderPage();
    expect(screen.getByText(`traffic:${AGENT_ID}:true`)).toBeInTheDocument();
  });
});

describe("AgentDetailPage — rename", () => {
  it("saves a new display name", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Rename/ }));
    const input = screen.getByDisplayValue("Deploy Bot");
    await userEvent.clear(input);
    await userEvent.type(input, "Release Bot");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() =>
      expect(h.update.mutateAsync).toHaveBeenCalledWith({
        agent_id: AGENT_ID,
        display_name: "Release Bot",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Agent name saved");
  });

  it("clears the name when saved empty", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Rename/ }));
    await userEvent.clear(screen.getByDisplayValue("Deploy Bot"));
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() =>
      expect(h.update.mutateAsync).toHaveBeenCalledWith({
        agent_id: AGENT_ID,
        display_name: null,
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Agent name cleared");
  });

  it("skips the mutation when the name is unchanged", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Rename/ }));
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(h.update.mutateAsync).not.toHaveBeenCalled();
    // The edit row closes.
    expect(screen.queryByDisplayValue("Deploy Bot")).not.toBeInTheDocument();
  });

  it("saves on Enter and cancels on Escape", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Rename/ }));
    const input = screen.getByDisplayValue("Deploy Bot");
    await userEvent.type(input, " 2{Enter}");
    await waitFor(() =>
      expect(h.update.mutateAsync).toHaveBeenCalledWith({
        agent_id: AGENT_ID,
        display_name: "Deploy Bot 2",
      }),
    );
  });

  it("labels the button 'Name agent' when unnamed and hides it for non-admins", () => {
    h.roster = { data: [] };
    renderPage();
    expect(screen.getByRole("button", { name: /Name agent/ })).toBeInTheDocument();
    h.me = { isAdmin: false, me: { username: "auditor" } };
    renderPage();
    expect(screen.queryByRole("button", { name: /Rename/ })).not.toBeInTheDocument();
  });

  it("surfaces save failures", async () => {
    h.update.mutateAsync = vi.fn().mockRejectedValue(new Error("403 forbidden"));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Rename/ }));
    await userEvent.type(screen.getByDisplayValue("Deploy Bot"), " 2{Enter}");
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("403 forbidden"));
  });
});

describe("AgentDetailPage — admin actions", () => {
  it("blocks the agent after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    await waitFor(() =>
      expect(h.block.mutateAsync).toHaveBeenCalledWith({
        agent_id: AGENT_ID,
        reason: "Blocked from Agent detail by admin-user",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(`Agent ${AGENT_ID} blocked`);
  });

  it("does nothing when the confirm is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    expect(h.block.mutateAsync).not.toHaveBeenCalled();
  });

  it("unblocks the agent and surfaces failures", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Unblock agent" }));
    await waitFor(() => expect(h.unblock.mutateAsync).toHaveBeenCalledWith(AGENT_ID));
    expect(h.toast.success).toHaveBeenCalledWith(`Agent ${AGENT_ID} unblocked`);

    h.unblock.mutateAsync = vi.fn().mockRejectedValue(new Error("500 nope"));
    await userEvent.click(screen.getByRole("button", { name: "Unblock agent" }));
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("500 nope"));
  });

  it("links to the pre-filtered audit log and chains pages", () => {
    renderPage();
    expect(
      screen.getByRole("link", { name: "View entries in Audit Log →" }),
    ).toHaveAttribute("href", `/audit-log?agent=${encodeURIComponent(AGENT_ID)}`);
    expect(screen.getByRole("link", { name: "View chains →" })).toHaveAttribute(
      "href",
      `/agent-chains?agent=${encodeURIComponent(AGENT_ID)}`,
    );
  });

  it("hides the admin section for non-admins", () => {
    h.me = { isAdmin: false, me: { username: "auditor" } };
    renderPage();
    expect(screen.queryByText("Admin actions")).not.toBeInTheDocument();
    expect(screen.getByText(`traffic:${AGENT_ID}:false`)).toBeInTheDocument();
  });
});
