import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { HostLabelRow } from "@/api/types";

const h = vi.hoisted(() => ({
  hosts: { data: undefined as HostLabelRow[] | undefined, isLoading: false },
}));
vi.mock("@/api/queries", () => ({ useHostLabels: () => h.hosts }));

import HostsListPage from "./hosts-list";

function row(overrides: Partial<HostLabelRow>): HostLabelRow {
  return {
    ip: "10.0.0.5",
    hostname: "build-01",
    source: "admin",
    resolved_at: null,
    last_seen: 1750000000,
    last_seen_iso: "2026-06-15T15:06:40Z",
    ...overrides,
  };
}

const ROWS: HostLabelRow[] = [
  row({ ip: "10.0.0.5", hostname: "build-01", source: "admin", last_seen: 300 }),
  row({ ip: "10.0.0.6", hostname: "printer.lan", source: "dns", last_seen: 200 }),
  row({
    ip: "10.0.0.7",
    hostname: null,
    source: "dns miss",
    last_seen: 100,
    last_seen_iso: null,
  }),
  row({ ip: "10.0.0.8", hostname: null, source: null, last_seen: null, last_seen_iso: null }),
];

function renderPage() {
  return render(
    <MemoryRouter>
      <HostsListPage />
    </MemoryRouter>,
  );
}

function hostColumn(): Array<string | undefined> {
  // First cell of each body row (skip the header row).
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].textContent ?? undefined);
}

beforeEach(() => {
  h.hosts = { data: ROWS, isLoading: false };
});

describe("HostsListPage", () => {
  it("shows skeletons while loading", () => {
    h.hosts = { data: undefined, isLoading: true };
    const { container } = renderPage();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the no-hosts empty state", () => {
    h.hosts = { data: [], isLoading: false };
    renderPage();
    expect(screen.getByText("No hosts observed yet.")).toBeInTheDocument();
  });

  it("computes KPI counts and chip totals", () => {
    renderPage();
    // "Labeled" also appears in a filter chip; the KPI tile comes first in
    // DOM order.
    const kpi = (label: string) =>
      screen.getAllByText(label)[0].closest("div")!.parentElement as HTMLElement;
    expect(within(kpi("Total hosts")).getByText("4")).toBeInTheDocument();
    expect(within(kpi("Labeled")).getByText("1")).toBeInTheDocument();
    expect(within(kpi("Labeled")).getByText("25% named")).toBeInTheDocument();
    expect(within(kpi("DNS misses")).getByText("1")).toBeInTheDocument();
    expect(
      within(kpi("DNS misses")).getByText("IPs with no PTR — label in Settings"),
    ).toBeInTheDocument();
    // Chip counters.
    expect(screen.getByRole("button", { name: "All 4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unresolved 1" })).toBeInTheDocument();
  });

  it("renders rows sorted by last_seen desc with NULLs last", () => {
    renderPage();
    expect(hostColumn()).toEqual([
      "build-01",
      "printer.lan",
      "(unresolved)",
      "(unresolved)",
    ]);
    // Detail-page links and source chips.
    expect(screen.getByRole("link", { name: "build-01" })).toHaveAttribute(
      "href",
      "/hosts/10.0.0.5",
    );
    expect(screen.getByText("dns miss")).toBeInTheDocument();
    expect(screen.getAllByText("2026-06-15 15:06:40").length).toBeGreaterThan(0);
  });

  it("filters by source chips", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Labeled 1" }));
    expect(hostColumn()).toEqual(["build-01"]);
    await userEvent.click(screen.getByRole("button", { name: "DNS miss 1" }));
    expect(screen.getAllByRole("row")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Unresolved 1" }));
    expect(screen.getByText("10.0.0.8")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "All 4" }));
    expect(hostColumn()).toHaveLength(4);
  });

  it("filters by IP or hostname search", async () => {
    renderPage();
    const input = screen.getByPlaceholderText("Filter by IP or hostname…");
    await userEvent.type(input, "printer");
    expect(hostColumn()).toEqual(["printer.lan"]);
    await userEvent.clear(input);
    await userEvent.type(input, "10.0.0.7");
    expect(hostColumn()).toEqual(["(unresolved)"]);
    await userEvent.clear(input);
    await userEvent.type(input, "does-not-exist");
    expect(
      screen.getByText("No hosts match the current filter."),
    ).toBeInTheDocument();
  });

  it("sorts by host name and flips direction on second click", async () => {
    renderPage();
    // First click: host asc (hostname, falling back to IP).
    await userEvent.click(screen.getByRole("button", { name: "Host" }));
    expect(hostColumn()).toEqual([
      "(unresolved)", // 10.0.0.7
      "(unresolved)", // 10.0.0.8
      "build-01",
      "printer.lan",
    ]);
    // Second click flips to desc.
    await userEvent.click(screen.getByRole("button", { name: /Host/ }));
    expect(hostColumn()).toEqual([
      "printer.lan",
      "build-01",
      "(unresolved)",
      "(unresolved)",
    ]);
  });

  it("sorts by source", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Source" }));
    // Desc by default for non-host columns: "dns miss" > "dns" > "admin" > "".
    const sources = screen
      .getAllByRole("row")
      .slice(1)
      .map((r) => within(r).getAllByRole("cell")[2].textContent);
    expect(sources).toEqual(["dns miss", "dns", "admin", "—"]);
  });
});
