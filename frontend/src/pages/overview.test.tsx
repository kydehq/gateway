import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  stats: { data: undefined as unknown, isLoading: true, dataUpdatedAt: 0 },
  verify: { data: undefined as unknown },
}));
vi.mock("@/api/queries", () => ({
  useStats: () => h.stats,
  useVerify: () => h.verify,
}));
// recharts is heavy and pulls layout APIs jsdom can't size; the page's
// charts aren't what we're asserting, so stub the chart container.
vi.mock("@/components/shared/chart-card", () => ({
  ChartCard: ({ title }: { title: string }) => <div>chart:{title}</div>,
}));

import OverviewPage from "./overview";

function renderPage() {
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.stats = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
  h.verify = { data: undefined };
});

describe("OverviewPage", () => {
  it("renders skeletons while stats are loading", () => {
    renderPage();
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.queryByText("Total Entries")).not.toBeInTheDocument();
  });

  it("renders metric cards and a VERIFIED integrity badge when the chain is valid", () => {
    h.stats = {
      data: {
        total: 100,
        first_entry: "2026-01-01",
        last_entry: "2026-06-01",
        unique_agents: 5,
        unique_sessions: 12,
        activity: { "2026-06-01": 10 },
        agents: { "agent:a": 60, "agent:b": 40 },
        action_types: { chat: 90, tool_call: 10 },
        upstreams: { openai: 100 },
      },
      isLoading: false,
      dataUpdatedAt: 1,
    };
    h.verify = { data: { valid: true } };
    renderPage();
    expect(screen.getByText("Total Entries")).toBeInTheDocument();
    expect(screen.getByText("VERIFIED")).toBeInTheDocument();
    expect(screen.getByText("chart:Activity Over Time")).toBeInTheDocument();
  });

  it("shows BROKEN when chain verification fails", () => {
    h.stats = {
      data: {
        total: 1, first_entry: null, last_entry: null, unique_agents: 1,
        unique_sessions: 1, activity: {}, agents: {}, action_types: {}, upstreams: {},
      },
      isLoading: false,
      dataUpdatedAt: 1,
    };
    h.verify = { data: { valid: false } };
    renderPage();
    expect(screen.getByText("BROKEN")).toBeInTheDocument();
  });
});
