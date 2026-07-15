import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AgentTrafficRow } from "@/api/types";

const h = vi.hoisted(() => ({
  traffic: {
    data: undefined as AgentTrafficRow[] | undefined,
    isLoading: false,
  },
  mutate: vi.fn(),
  isPending: false,
}));
vi.mock("@/api/queries", () => ({
  useAgentTraffic: () => h.traffic,
  useSetTrafficMode: () => ({ mutate: h.mutate, isPending: h.isPending }),
}));

import { TrafficInventory } from "./traffic-inventory";

function row(overrides: Partial<AgentTrafficRow>): AgentTrafficRow {
  return {
    path_kind: "embedding",
    count: 12,
    last_seen: new Date(Date.now() - 90_000).toISOString(),
    mode: "count_only",
    ...overrides,
  } as AgentTrafficRow;
}

beforeEach(() => {
  h.traffic = { data: undefined, isLoading: false };
  h.mutate = vi.fn();
  h.isPending = false;
});

describe("TrafficInventory", () => {
  it("shows skeletons while loading", () => {
    h.traffic = { data: undefined, isLoading: true };
    const { container } = render(<TrafficInventory agentId="a" isAdmin />);
    expect(screen.getByText("Traffic inventory")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state when no traffic exists", () => {
    h.traffic = { data: [], isLoading: false };
    render(<TrafficInventory agentId="a" isAdmin />);
    expect(
      screen.getByText("No traffic recorded yet for this agent."),
    ).toBeInTheDocument();
  });

  it("renders rows with label, count, relative time and mode badge", () => {
    h.traffic = {
      isLoading: false,
      data: [
        row({ path_kind: "chat", count: 1234, mode: "full_logging" }),
        row({ path_kind: "embedding", count: 5, last_seen: null }),
      ],
    };
    render(<TrafficInventory agentId="a" isAdmin />);
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.getByText("Embeddings")).toBeInTheDocument();
    expect(screen.getByText((1234).toLocaleString())).toBeInTheDocument();
    // "full logging" also appears in the header description; the badge is
    // the second occurrence.
    expect(screen.getAllByText("full logging").length).toBeGreaterThan(1);
    expect(screen.getByText("count only")).toBeInTheDocument();
    // Null last_seen renders as an em dash.
    expect(screen.getByText("—")).toBeInTheDocument();
    // 90s ago → minutes bucket.
    expect(screen.getByText("1m ago")).toBeInTheDocument();
  });

  it("falls back to the Unclassified label for unknown path kinds", () => {
    h.traffic = {
      isLoading: false,
      data: [row({ path_kind: "brand_new_kind" as AgentTrafficRow["path_kind"] })],
    };
    render(<TrafficInventory agentId="a" isAdmin={false} />);
    expect(screen.getByText("Unclassified")).toBeInTheDocument();
  });

  it("hides the mode toggle for the chat row and for non-admins", () => {
    h.traffic = {
      isLoading: false,
      data: [row({ path_kind: "chat" }), row({ path_kind: "embedding" })],
    };
    const { rerender } = render(<TrafficInventory agentId="a" isAdmin />);
    // Only the embedding row gets a toggle.
    expect(screen.getAllByRole("button")).toHaveLength(1);

    rerender(<TrafficInventory agentId="a" isAdmin={false} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("flips count_only → full_logging via the toggle", async () => {
    h.traffic = { isLoading: false, data: [row({ mode: "count_only" })] };
    render(<TrafficInventory agentId="a" isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Enable logging" }));
    expect(h.mutate).toHaveBeenCalledWith({
      path_kind: "embedding",
      mode: "full_logging",
    });
  });

  it("flips full_logging → count_only via the toggle", async () => {
    h.traffic = { isLoading: false, data: [row({ mode: "full_logging" })] };
    render(<TrafficInventory agentId="a" isAdmin />);
    await userEvent.click(screen.getByRole("button", { name: "Disable logging" }));
    expect(h.mutate).toHaveBeenCalledWith({
      path_kind: "embedding",
      mode: "count_only",
    });
  });
});
