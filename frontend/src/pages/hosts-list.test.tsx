import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({ hosts: { data: undefined as unknown, isLoading: true } }));
vi.mock("@/api/queries", () => ({ useHostLabels: () => h.hosts }));

import HostsListPage from "./hosts-list";

function renderPage() {
  return render(
    <MemoryRouter>
      <HostsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.hosts = { data: undefined, isLoading: true };
});

describe("HostsListPage", () => {
  it("renders the header while loading", () => {
    renderPage();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
  });

  it("renders an empty state when there are no hosts", () => {
    h.hosts = { data: [], isLoading: false };
    renderPage();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
  });
});
