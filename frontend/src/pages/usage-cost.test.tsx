import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  tokens: { data: undefined as unknown, isLoading: true, dataUpdatedAt: 0 },
}));
vi.mock("@/api/queries", () => ({ useTokenAnalysis: () => h.tokens }));
vi.mock("@/hooks/use-agent-label", () => ({
  useAgentLabel: () => ({ shortLabel: (s: string) => s }),
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

function renderPage() {
  return render(
    <MemoryRouter>
      <UsageCostPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.tokens = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
});

describe("UsageCostPage", () => {
  it("renders the loading state", () => {
    renderPage();
    expect(screen.getByText("Token Usage")).toBeInTheDocument();
    expect(screen.queryByText("Total Tokens")).not.toBeInTheDocument();
  });

  it("renders KPI cards once token data has loaded", () => {
    h.tokens = {
      data: {
        total_tokens: 1500,
        total_prompt_tokens: 1000,
        total_completion_tokens: 500,
        by_hour: {},
        by_agent: {},
        by_model: {},
        by_upstream: {},
      },
      isLoading: false,
      dataUpdatedAt: 1,
    };
    renderPage();
    expect(screen.getByText("Total Tokens")).toBeInTheDocument();
  });
});
