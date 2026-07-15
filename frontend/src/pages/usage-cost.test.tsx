import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { TokenAnalysis } from "@/api/types";

const h = vi.hoisted(() => ({
  tokens: {
    data: undefined as TokenAnalysis | undefined,
    isLoading: false,
    dataUpdatedAt: 1,
  },
}));
vi.mock("@/api/queries", () => ({ useTokenAnalysis: () => h.tokens }));
vi.mock("@/hooks/use-agent-label", () => ({
  useAgentLabel: () => ({ shortLabel: (s: string) => `short(${s})` }),
}));
// recharts needs real layout sizing jsdom can't provide; stub the exports
// this page imports. (A Proxy that answers every key would also answer
// `then`, making the module look like a thenable and hanging the import.)
vi.mock("recharts", () => {
  const Stub = () => null;
  return {
    Bar: Stub,
    BarChart: Stub,
    CartesianGrid: Stub,
    Legend: Stub,
    ReferenceLine: Stub,
    ResponsiveContainer: Stub,
    Tooltip: Stub,
    XAxis: Stub,
    YAxis: Stub,
  };
});
vi.mock("@/components/shared/chart-card", () => ({
  ChartCard: ({ title }: { title: string }) => <div>chart:{title}</div>,
}));

import UsageCostPage from "./usage-cost";

function bucket(prompt: number, completion: number) {
  return { prompt_tokens: prompt, completion_tokens: completion };
}

function analysis(overrides: Partial<TokenAnalysis>): TokenAnalysis {
  return {
    total_tokens: 1500,
    total_prompt_tokens: 1000,
    total_completion_tokens: 500,
    by_hour: { "2026-07-01T09:00": bucket(600, 300), "2026-07-01T10:00": bucket(400, 200) },
    by_agent: {
      "agent:a": bucket(600, 300),
      "agent:b": bucket(400, 200),
    },
    by_model: { "gpt-x": bucket(1000, 500) },
    by_upstream: { "api.openai.com": bucket(1000, 500) },
    ...overrides,
  } as TokenAnalysis;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <UsageCostPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.tokens = { data: analysis({}), isLoading: false, dataUpdatedAt: 1 };
});

describe("UsageCostPage", () => {
  it("renders the loading state without KPIs", () => {
    h.tokens = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
    const { container } = renderPage();
    expect(screen.getByText("Token Usage")).toBeInTheDocument();
    expect(screen.queryByText("Total Tokens")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("renders the KPI values", () => {
    renderPage();
    const kpi = (label: string) => screen.getByText(label).parentElement as HTMLElement;
    expect(kpi("Total Tokens")).toHaveTextContent("1.5K");
    expect(kpi("Prompt / Completion")).toHaveTextContent("1.0K / 500");
    expect(kpi("Active Agents")).toHaveTextContent("2");
  });

  it("renders all four chart cards", () => {
    renderPage();
    expect(screen.getByText("chart:Token Usage Over Time")).toBeInTheDocument();
    expect(screen.getByText("chart:By Agent")).toBeInTheDocument();
    expect(screen.getByText("chart:By Model")).toBeInTheDocument();
    expect(screen.getByText("chart:By AI Provider")).toBeInTheDocument();
  });

  it("renders the agent breakdown table with derived labels, sorted by total", () => {
    renderPage();
    expect(screen.getByText("Agent Breakdown")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("short(agent:a)")).toBeInTheDocument();
    expect(within(rows[0]).getByText("600")).toBeInTheDocument();
    expect(within(rows[0]).getByText("300")).toBeInTheDocument();
    expect(within(rows[0]).getByText("900")).toBeInTheDocument();
    expect(within(rows[1]).getByText("short(agent:b)")).toBeInTheDocument();
  });

  it("collapses long agent tables behind a show-more button", async () => {
    const many: Record<string, { prompt_tokens: number; completion_tokens: number }> = {};
    for (let i = 0; i < 13; i++) many[`agent:${String(i).padStart(2, "0")}`] = bucket(100 - i, 0);
    h.tokens = { data: analysis({ by_agent: many }), isLoading: false, dataUpdatedAt: 1 };
    renderPage();
    // 10 visible + header row.
    expect(screen.getAllByRole("row")).toHaveLength(11);
    await userEvent.click(
      screen.getByRole("button", { name: "Show 3 more agents →" }),
    );
    expect(screen.getAllByRole("row")).toHaveLength(14);
  });

  it("handles empty token data", () => {
    h.tokens = {
      data: analysis({
        total_tokens: 0,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        by_hour: {},
        by_agent: {},
        by_model: {},
        by_upstream: {},
      }),
      isLoading: false,
      dataUpdatedAt: 1,
    };
    renderPage();
    const kpi = (label: string) => screen.getByText(label).parentElement as HTMLElement;
    expect(kpi("Total Tokens")).toHaveTextContent("0");
    expect(kpi("Active Agents")).toHaveTextContent("0");
  });
});
