import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Agent, DlpAlert, Stats, TokenAnalysis, TopologyAgent } from "@/api/types";

const h = vi.hoisted(() => ({
  stats: { data: undefined as Stats | undefined, isLoading: false, dataUpdatedAt: 1 },
  tokens: { data: undefined as TokenAnalysis | undefined, isLoading: false },
  agents: { data: [] as Agent[] },
  alerts: { data: [] as DlpAlert[] },
  topoAgent: { data: undefined as TopologyAgent | undefined, isLoading: false },
  topoAgentId: null as unknown,
  blockAgent: { mutateAsync: vi.fn(), isPending: false },
  me: { me: { username: "kim" } as unknown, isAdmin: false },
  features: { enforcementEnabled: true },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useStats: () => h.stats,
  useTokenAnalysis: () => h.tokens,
  useAgents: () => h.agents,
  useDlpAlerts: () => h.alerts,
  useTopologyAgent: (agentId: unknown) => {
    h.topoAgentId = agentId;
    return h.topoAgent;
  },
  useBlockAgent: () => h.blockAgent,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("sonner", () => ({ toast: h.toast }));
// recharts needs real layout sizes jsdom can't provide; the charts aren't
// under test, so stub every primitive. ChartCard stays real so titles,
// subtitles, and empty-state children still render.
vi.mock("recharts", () => {
  const Noop = () => null;
  return {
    ResponsiveContainer: Noop,
    Bar: Noop,
    BarChart: Noop,
    CartesianGrid: Noop,
    Cell: Noop,
    Line: Noop,
    LineChart: Noop,
    PieChart: Noop,
    Pie: Noop,
    ReferenceLine: Noop,
    Tooltip: Noop,
    XAxis: Noop,
    YAxis: Noop,
    Legend: Noop,
  };
});

import AgentActivityPage from "./agent-activity";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

function makeStats(overrides: Partial<Stats>): Stats {
  return {
    total: 100,
    first_entry: null,
    last_entry: null,
    unique_agents: 3,
    unique_sessions: 12,
    activity: {},
    agents: {},
    action_types: {},
    upstreams: {},
    ...overrides,
  } as Stats;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentActivityPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.stats = { data: makeStats({}), isLoading: false, dataUpdatedAt: 1 };
  h.tokens = {
    isLoading: false,
    data: {
      total_tokens: 1_234_567,
      by_agent: {},
      by_model: {},
      by_upstream: {},
    } as unknown as TokenAnalysis,
  };
  h.agents = { data: [] };
  h.alerts = { data: [] };
  h.topoAgent = { data: undefined, isLoading: false };
  h.topoAgentId = "unset";
  h.blockAgent = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.me = { me: { username: "kim" }, isAdmin: false };
  h.features = { enforcementEnabled: true };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("AgentActivityPage — KPIs and charts", () => {
  it("renders skeletons while stats load", () => {
    h.stats = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
    const { container } = renderPage();
    expect(screen.getByText("Agent Activity")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("renders KPI cards from stats and token totals", () => {
    renderPage();
    expect(screen.getByText("Active Agents").closest("a")).toHaveAttribute(
      "href",
      "/agents",
    );
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    // 1_234_567 → "1.2M"
    expect(screen.getByText("1.2M")).toBeInTheDocument();
  });

  it("counts only open alerts inside the selected window", () => {
    h.alerts = {
      data: [
        { id: 1, status: "new", created_dt: iso(2 * DAY) } as DlpAlert,
        { id: 2, status: "closed", created_dt: iso(2 * DAY) } as DlpAlert,
        { id: 3, status: "new", created_dt: iso(60 * DAY) } as DlpAlert,
        { id: 4, status: "new", created_dt: "not-a-date" } as DlpAlert,
      ],
    };
    renderPage();
    const card = screen.getByText("Open Alerts (30d)").closest("a")!;
    expect(within(card as HTMLElement).getByText("1")).toBeInTheDocument();
  });

  it("flags volume outliers >2σ from the window mean in the chart subtitle", () => {
    const activity: Record<string, number> = {};
    for (let d = 1; d <= 6; d++) activity[`2026-06-0${d}`] = 0;
    activity["2026-06-07"] = 100;
    h.stats = { data: makeStats({ activity }), isLoading: false, dataUpdatedAt: 1 };
    renderPage();
    expect(screen.getByText(/1 volume outlier /)).toBeInTheDocument();
  });

  it("reports a calm window when no outliers exist", () => {
    h.stats = {
      data: makeStats({ activity: { "2026-06-01": 10, "2026-06-02": 11 } }),
      isLoading: false,
      dataUpdatedAt: 1,
    };
    renderPage();
    expect(
      screen.getByText("No volume outliers in this window"),
    ).toBeInTheDocument();
  });

  it("shows the action-mix empty state when the window has no actions", () => {
    renderPage();
    expect(screen.getByText("No actions in this window.")).toBeInTheDocument();
  });
});

describe("AgentActivityPage — agent table", () => {
  function seedAgents() {
    h.tokens = {
      isLoading: false,
      data: {
        total_tokens: 5000,
        by_agent: {
          "agent:a": { prompt_tokens: 2000, completion_tokens: 500, requests: 40 },
          "agent:b": { prompt_tokens: 100, completion_tokens: 50, requests: 90 },
        },
        by_model: {},
        by_upstream: {},
      } as unknown as TokenAnalysis,
    };
    h.agents = {
      data: [
        {
          agent_id: "agent:a",
          display_name: "Builder",
          first_seen: 0,
          last_seen: 0,
          first_seen_dt: "2026-06-01T08:00:00Z",
          last_seen_dt: iso(HOUR),
          entry_count: 40,
          session_count: 6,
        },
        // Roster-only agent (no token bucket) — renders with "—" columns.
        {
          agent_id: "agent:c",
          display_name: null,
          first_seen: 0,
          last_seen: 0,
          first_seen_dt: "2026-05-01T08:00:00Z",
          last_seen_dt: iso(3 * DAY),
          entry_count: 1,
          session_count: 0,
        },
      ],
    };
  }

  it("merges token buckets with the roster and marks recent agents active", () => {
    seedAgents();
    renderPage();
    // display_name from the roster wins over the hash label.
    expect(screen.getByText("Builder")).toBeInTheDocument();
    // 2500 tokens → "2.5K"
    expect(screen.getByText("2.5K")).toBeInTheDocument();
    expect(screen.getByText("2026-06-01")).toBeInTheDocument();
    // agent:a seen 1h ago → active; agent:c 3d ago → idle.
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getAllByText("idle").length).toBeGreaterThan(0);
    // Roster-only agent has zero sessions → em dash.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("switches between token and call metrics", async () => {
    seedAgents();
    renderPage();
    expect(screen.getByText("Agent Detail (by Tokens)")).toBeInTheDocument();
    expect(screen.getByText("Top Agents by Tokens")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Calls" }));
    expect(screen.getByText("Agent Detail (by Calls)")).toBeInTheDocument();
    expect(screen.getByText("Top Agents by Calls")).toBeInTheDocument();
    // Calls view shows raw request counts.
    expect(screen.getByText("90")).toBeInTheDocument();
  });
});

describe("AgentActivityPage — detail dialog", () => {
  function seedOneAgent() {
    h.tokens = {
      isLoading: false,
      data: {
        total_tokens: 100,
        by_agent: {
          "agent:a": { prompt_tokens: 80, completion_tokens: 20, requests: 4 },
        },
        by_model: {},
        by_upstream: {},
      } as unknown as TokenAnalysis,
    };
  }

  const topoData: TopologyAgent = {
    agent_id: "agent:a",
    window: "30d",
    request_count: 4,
    first_seen: null,
    first_seen_iso: null,
    last_seen: null,
    last_seen_iso: "2026-07-01T10:00:00Z",
    segments: [],
    ips: [],
    tools: [{ name: "read_file", count: 3, request_count: 3 }],
    upstreams: [{ name: "openai", count: 4, request_count: 4 }],
    models: [{ name: "gpt-x", count: 4, request_count: 4 }],
    sessions: [{ session_id: "sess-1", request_count: 4 }] as TopologyAgent["sessions"],
  };

  async function openDialog() {
    renderPage();
    // The cell shows the derived label ("Claude Code Agent (a)"); the raw
    // id lives on the title attribute.
    await userEvent.click(screen.getByTitle("agent:a"));
  }

  it("opens on row click and shows the per-agent breakdown", async () => {
    seedOneAgent();
    h.topoAgent = { data: topoData, isLoading: false };
    await openDialog();
    expect(h.topoAgentId).toBe("agent:a");
    expect(screen.getByText(/4 requests/)).toBeInTheDocument();
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByText("gpt-x")).toBeInTheDocument();
    expect(screen.getByText("sess-1")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open full view ↗" }),
    ).toHaveAttribute("href", "/agents/agent%3Aa");
    // No block button for non-admins.
    expect(
      screen.queryByRole("button", { name: "Block agent" }),
    ).not.toBeInTheDocument();
    // Radix injects its own sr-only "Close" (the X); the footer button is last.
    const closeBtns = screen.getAllByRole("button", { name: "Close" });
    await userEvent.click(closeBtns[closeBtns.length - 1]);
    await waitFor(() =>
      expect(screen.queryByText("Recent sessions")).not.toBeInTheDocument(),
    );
  });

  it("shows a loading placeholder while the breakdown fetches", async () => {
    seedOneAgent();
    h.topoAgent = { data: undefined, isLoading: true };
    await openDialog();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows empty notes when the agent has no observed usage", async () => {
    seedOneAgent();
    h.topoAgent = {
      isLoading: false,
      data: { ...topoData, tools: [], upstreams: [], models: [], sessions: [] },
    };
    await openDialog();
    expect(screen.getByText("No model usage observed.")).toBeInTheDocument();
    expect(screen.getByText("No tools observed.")).toBeInTheDocument();
    expect(screen.getByText("No upstream data.")).toBeInTheDocument();
    expect(screen.getByText("No recent sessions.")).toBeInTheDocument();
  });

  it("lets admins block the agent after confirmation", async () => {
    seedOneAgent();
    h.me = { me: { username: "kim" }, isAdmin: true };
    h.topoAgent = { data: topoData, isLoading: false };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    await waitFor(() =>
      expect(h.blockAgent.mutateAsync).toHaveBeenCalledWith({
        agent_id: "agent:a",
        reason: "Blocked from Agent Detail by kim",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Agent agent:a blocked");
  });

  it("keeps the agent when the admin cancels, and surfaces block failures", async () => {
    seedOneAgent();
    h.me = { me: { username: "kim" }, isAdmin: true };
    h.topoAgent = { data: topoData, isLoading: false };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    expect(h.blockAgent.mutateAsync).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    h.blockAgent.mutateAsync = vi.fn().mockRejectedValue(new Error("403 forbidden"));
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("403 forbidden"));
  });
});
