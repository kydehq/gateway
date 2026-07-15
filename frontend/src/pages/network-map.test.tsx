import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { Stats, TopologyFlow, TopologyResponse } from "@/api/types";

const h = vi.hoisted(() => ({
  stats: { data: undefined as Stats | undefined, isLoading: false },
  topology: {
    data: undefined as TopologyResponse | undefined,
    isLoading: false,
    dataUpdatedAt: 1,
  },
  flow: { data: undefined as TopologyFlow | undefined, isLoading: false },
  flowArgs: [] as unknown[],
}));
vi.mock("@/api/queries", () => ({
  useStats: () => h.stats,
  useTopology: () => h.topology,
  useTopologyFlow: (...args: unknown[]) => {
    h.flowArgs = args;
    return h.flow;
  },
}));
// The Sankey/pie SVGs need real layout; stub the recharts primitives and
// assert on the data-driven chrome around them (KPIs, legend, tables, sheet).
vi.mock("recharts", () => {
  const Noop = () => null;
  return {
    ResponsiveContainer: Noop,
    Sankey: Noop,
    Tooltip: Noop,
    PieChart: Noop,
    Pie: Noop,
    Cell: Noop,
  };
});

import NetworkMapPage from "./network-map";

const topo: TopologyResponse = {
  window: "24h",
  min_value: 1,
  nodes: [
    { id: "seg:10.0.0.0/24", layer: "segment", label: "10.0.0.0/24", meta: { class: "rfc1918" } },
    { id: "agent:a", layer: "agent", label: "agent-a" },
    { id: "agent:unknown", layer: "agent", label: "unknown" },
    { id: "gw", layer: "gateway", label: "KYDE Gateway" },
    { id: "up:openai", layer: "upstream", label: "api.openai.com" },
    { id: "model:gpt-x", layer: "model", label: "gpt-x" },
    { id: "model:claude", layer: "model", label: "claude-3" },
  ],
  links: [
    { source: "seg:10.0.0.0/24", target: "agent:a", value: 60 },
    { source: "seg:10.0.0.0/24", target: "agent:unknown", value: 40 },
    { source: "agent:a", target: "gw", value: 60 },
    { source: "gw", target: "up:openai", value: 100 },
    { source: "up:openai", target: "model:gpt-x", value: 70 },
    { source: "up:openai", target: "model:claude", value: 30 },
    // Dangling link (missing node) — must be dropped by toSankeyData.
    { source: "ghost", target: "gw", value: 5 },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <NetworkMapPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.stats = { data: undefined, isLoading: false };
  h.topology = { data: topo, isLoading: false, dataUpdatedAt: 1 };
  h.flow = { data: undefined, isLoading: false };
  h.flowArgs = [];
});

describe("NetworkMapPage", () => {
  it("shows a skeleton while topology loads", () => {
    h.topology = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
    const { container } = renderPage();
    expect(screen.getByText("Network Map")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("computes the KPI cards from topology layers", () => {
    renderPage();
    const kpi = (label: string) =>
      within(screen.getByText(label).closest("div")!.parentElement as HTMLElement);
    expect(kpi("Total Nodes").getByText("7")).toBeInTheDocument();
    expect(kpi("Network Segments").getByText("1")).toBeInTheDocument();
    expect(kpi("AI Providers").getByText("1")).toBeInTheDocument();
    expect(kpi("Models").getByText("2")).toBeInTheDocument();
    expect(kpi("Unknowns").getByText("1")).toBeInTheDocument();
    expect(screen.getByText("unattributed nodes")).toBeInTheDocument();
  });

  it("shows the empty notice when the window has no links", () => {
    h.topology = {
      data: { ...topo, links: [] },
      isLoading: false,
      dataUpdatedAt: 1,
    };
    renderPage();
    expect(
      screen.getByText("No traffic in the selected window."),
    ).toBeInTheDocument();
  });

  it("renders a legend entry per model node", () => {
    renderPage();
    expect(screen.getByText("flows by model:")).toBeInTheDocument();
    expect(screen.getByText("gpt-x")).toBeInTheDocument();
    expect(screen.getByText("claude-3")).toBeInTheDocument();
  });

  it("lists unattributed nodes with their incoming request totals", () => {
    renderPage();
    expect(screen.getByText("Unattributed nodes")).toBeInTheDocument();
    expect(screen.getByText(/1 layer need labels/)).toBeInTheDocument();
    const row = screen.getByText("agent", { selector: "td" }).closest("tr")!;
    expect(within(row).getByText("40")).toBeInTheDocument();
  });

  it("opens the flow drill-down from an Investigate link", async () => {
    h.flow = {
      isLoading: false,
      data: {
        source_layer: "segment",
        source_label: "10.0.0.0/24",
        target_layer: "agent",
        target_label: "unknown",
        window: "24h",
        request_count: 40,
        first_seen_iso: "2026-07-01T08:00:00Z",
        last_seen_iso: "2026-07-01T09:30:00Z",
        agents: [
          {
            agent_id: "agent:a",
            display_name: "Builder",
            request_count: 25,
            last_seen_iso: "2026-07-01T09:30:00Z",
          },
        ],
        sessions: [
          {
            session_id: "sess-1",
            serial_id: 3,
            request_count: 12,
            last_seen_iso: "2026-07-01T09:30:00Z",
          },
        ],
      },
    };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Investigate →" }));
    // Query fires with the clicked link's endpoints.
    expect(h.flowArgs[0]).toEqual({ layer: "segment", label: "10.0.0.0/24" });
    expect(h.flowArgs[1]).toEqual({ layer: "agent", label: "unknown" });
    // Panel contents — scope to the sheet since "40" also appears in the
    // unattributed-nodes table behind it.
    const panel = within(screen.getByRole("dialog"));
    expect(panel.getByText("40")).toBeInTheDocument();
    expect(panel.getByText(/2026-07-01 08:00:00/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Builder" })).toHaveAttribute(
      "href",
      "/agents/agent%3Aa",
    );
    expect(screen.getByRole("link", { name: "SES-0003" })).toHaveAttribute(
      "href",
      "/sessions/sess-1",
    );
    // Closing the panel clears the selection (Radix adds its own sr-only
    // "Close" X — the explicit footer button is last).
    const closeBtns = screen.getAllByRole("button", { name: "Close" });
    await userEvent.click(closeBtns[closeBtns.length - 1]);
    await waitFor(() =>
      expect(screen.queryByText("Top agents")).not.toBeInTheDocument(),
    );
  });

  it("shows empty notes for a flow with no agents or sessions", async () => {
    h.flow = {
      isLoading: false,
      data: {
        source_layer: "segment",
        source_label: "10.0.0.0/24",
        target_layer: "agent",
        target_label: "unknown",
        window: "24h",
        request_count: 0,
        first_seen_iso: null,
        last_seen_iso: null,
        agents: [],
        sessions: [],
      },
    };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Investigate →" }));
    expect(screen.getByText("No agents in this flow.")).toBeInTheDocument();
    expect(screen.getByText("No sessions in this flow.")).toBeInTheDocument();
  });

  it("shows the flow panel loading state while the drill-down fetches", async () => {
    h.flow = { data: undefined, isLoading: true };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Investigate →" }));
    // The sheet title rewrites labels (unknown stays raw for non-upstream).
    expect(
      within(screen.getByRole("dialog")).getByText(/10\.0\.0\.0\/24/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Top agents")).not.toBeInTheDocument();
  });

  it("renders the provider distribution from stats", () => {
    h.stats = {
      isLoading: false,
      data: {
        upstreams: {
          "api.openai.com": 70,
          "api.anthropic.com": 20,
          "(none)": 10,
        },
      } as unknown as Stats,
    };
    renderPage();
    expect(screen.getByText("AI Provider Distribution")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("Direct (no provider)")).toBeInTheDocument();
    expect(screen.getByText("70")).toBeInTheDocument();
  });
});
