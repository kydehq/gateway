import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  stats: { data: undefined as unknown, isLoading: true },
  topology: { data: undefined as unknown, isLoading: true },
  flow: { data: undefined as unknown, isLoading: true },
}));
vi.mock("@/api/queries", () => ({
  useStats: () => h.stats,
  useTopology: () => h.topology,
  useTopologyFlow: () => h.flow,
}));

import NetworkMapPage from "./network-map";

beforeEach(() => {
  h.stats = { data: undefined, isLoading: true };
  h.topology = { data: undefined, isLoading: true };
  h.flow = { data: undefined, isLoading: true };
});

describe("NetworkMapPage", () => {
  it("renders the header", () => {
    render(
      <MemoryRouter>
        <NetworkMapPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Network Map")).toBeInTheDocument();
  });
});
