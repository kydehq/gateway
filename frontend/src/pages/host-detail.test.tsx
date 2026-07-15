import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { HostResolveResponse, TopologyIp } from "@/api/types";

const h = vi.hoisted(() => ({
  ipQuery: {
    data: undefined as TopologyIp | undefined,
    isLoading: false,
    isError: false,
  },
  resolve: {
    data: undefined as HostResolveResponse | undefined,
    isLoading: false,
    isError: false,
  },
  upsert: { mutateAsync: vi.fn(), isPending: false },
  del: { mutateAsync: vi.fn(), isPending: false },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useTopologyIp: () => h.ipQuery,
  useHostResolve: () => h.resolve,
  useUpsertHostLabel: () => h.upsert,
  useDeleteHostLabel: () => h.del,
}));
vi.mock("sonner", () => ({ toast: h.toast }));

import HostDetailPage from "./host-detail";

const ipData: TopologyIp = {
  ip: "10.0.0.5",
  hostname: "build-01",
  hostname_source: "admin",
  class: "rfc1918",
  subnet: "10.0.0.0/24",
  window: "30d",
  request_count: 120,
  first_seen: 1,
  first_seen_iso: "2026-06-01T08:00:00Z",
  last_seen: 2,
  last_seen_iso: "2026-07-01T09:00:00Z",
  agents: [
    {
      agent_id: "agent:a",
      request_count: 100,
      first_seen: 1,
      last_seen: 2,
      first_seen_iso: "2026-06-01T08:00:00Z",
      last_seen_iso: "2026-07-01T09:00:00Z",
      tools: ["cursor", "claude-code"],
    },
  ],
  tools: [{ tool: "cursor", request_count: 100 }],
  upstreams: [{ upstream: "api.openai.com", request_count: 120 }],
  models: [{ model: "gpt-x", request_count: 120 }],
  sessions: [
    {
      session_id: "sess-1",
      request_count: 12,
      last_seen_iso: "2026-07-01T09:00:00Z",
      model: "gpt-x",
    } as TopologyIp["sessions"][number],
  ],
};

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

function renderPage(identifier: string) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/hosts/${encodeURIComponent(identifier)}`]}>
        <Routes>
          <Route
            path="/hosts/:identifier"
            element={
              <>
                <HostDetailPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.ipQuery = { data: ipData, isLoading: false, isError: false };
  h.resolve = { data: undefined, isLoading: false, isError: false };
  h.upsert = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.del = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("HostDetailPage — IP branch", () => {
  it("shows skeletons while loading", () => {
    h.ipQuery = { data: undefined, isLoading: true, isError: false };
    const { container } = renderPage("10.0.0.5");
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the error state with a back link", () => {
    h.ipQuery = { data: undefined, isLoading: false, isError: true };
    renderPage("10.0.0.5");
    expect(screen.getByText("Failed to load host detail.")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "← Back to Network Map" }),
    ).toHaveAttribute("href", "/network-map");
  });

  it("renders hostname, class chip, KPIs, and breakdown tables", () => {
    renderPage("10.0.0.5");
    expect(screen.getByText("build-01")).toBeInTheDocument();
    expect(screen.getByText("labeled")).toBeInTheDocument();
    expect(screen.getByText("RFC1918 (private)")).toBeInTheDocument();
    expect(screen.getByText("· subnet 10.0.0.0/24")).toBeInTheDocument();
    // Request count shows in the KPI and again in the breakdown values.
    expect(screen.getAllByText("120").length).toBeGreaterThan(0);
    expect(screen.getByText("2026-06-01")).toBeInTheDocument();
    // Agents table with link + tools.
    expect(screen.getByRole("link", { name: "agent:a" })).toHaveAttribute(
      "href",
      "/agents/agent%3Aa",
    );
    expect(screen.getByText("cursor, claude-code")).toBeInTheDocument();
    // Breakdown cards.
    expect(screen.getByText("cursor")).toBeInTheDocument();
    expect(screen.getByText("api.openai.com")).toBeInTheDocument();
    expect(screen.getByText("gpt-x", { selector: "span" })).toBeInTheDocument();
    // Sessions table.
    expect(screen.getByRole("link", { name: "sess-1" })).toHaveAttribute(
      "href",
      "/sessions/sess-1",
    );
  });

  it("shows the quiet state when the IP had no traffic", () => {
    h.ipQuery = {
      isLoading: false,
      isError: false,
      data: { ...ipData, request_count: 0 },
    };
    renderPage("10.0.0.5");
    expect(
      screen.getByText("No traffic observed from this IP in the last 30 days."),
    ).toBeInTheDocument();
  });

  it("edits the admin label and saves it", async () => {
    renderPage("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Edit name/ }));
    const input = screen.getByPlaceholderText("crm.internal");
    // Pre-filled with the current admin label.
    expect(input).toHaveValue("build-01");
    await userEvent.clear(input);
    await userEvent.type(input, "crm-db");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() =>
      expect(h.upsert.mutateAsync).toHaveBeenCalledWith({
        ip: "10.0.0.5",
        hostname: "crm-db",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Name set for 10.0.0.5");
  });

  it("rejects an empty name", async () => {
    renderPage("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Edit name/ }));
    await userEvent.clear(screen.getByPlaceholderText("crm.internal"));
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(h.toast.error).toHaveBeenCalledWith("Host name is required");
    expect(h.upsert.mutateAsync).not.toHaveBeenCalled();
  });

  it("clears the admin label", async () => {
    renderPage("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Edit name/ }));
    await userEvent.click(screen.getByRole("button", { name: /Clear label/ }));
    await waitFor(() =>
      expect(h.del.mutateAsync).toHaveBeenCalledWith("10.0.0.5"),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Cleared name for 10.0.0.5");
  });

  it("shows 'Set name' and no clear button for DNS-derived hostnames", async () => {
    h.ipQuery = {
      isLoading: false,
      isError: false,
      data: { ...ipData, hostname_source: "dns" },
    };
    renderPage("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Set name/ }));
    // DNS names don't pre-fill the draft, and there is nothing to clear.
    expect(screen.getByPlaceholderText("crm.internal")).toHaveValue("");
    expect(
      screen.queryByRole("button", { name: /Clear label/ }),
    ).not.toBeInTheDocument();
  });

  it("surfaces save failures", async () => {
    h.upsert.mutateAsync = vi.fn().mockRejectedValue(new Error("409 conflict"));
    renderPage("10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Edit name/ }));
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() =>
      expect(h.toast.error).toHaveBeenCalledWith("409 conflict"),
    );
  });
});

describe("HostDetailPage — hostname branch", () => {
  it("shows the unknown-hostname state", () => {
    h.resolve = {
      isLoading: false,
      isError: false,
      data: { kind: "hostname", hostname: "ghost.internal", ips: [] },
    };
    renderPage("ghost.internal");
    expect(screen.getByText("Unknown hostname.")).toBeInTheDocument();
    expect(screen.getByText("ghost.internal")).toBeInTheDocument();
  });

  it("auto-redirects when the hostname maps to exactly one IP", async () => {
    h.resolve = {
      isLoading: false,
      isError: false,
      data: {
        kind: "hostname",
        hostname: "build-01",
        ips: [{ ip: "10.0.0.5", source: "admin", last_seen: 1750000000 }],
      },
    };
    renderPage("build-01");
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/hosts/10.0.0.5"),
    );
  });

  it("renders a picker when the hostname maps to several IPs", () => {
    h.resolve = {
      isLoading: false,
      isError: false,
      data: {
        kind: "hostname",
        hostname: "roaming-laptop",
        ips: [
          { ip: "10.0.0.5", source: "admin", last_seen: 1750000000 },
          { ip: "10.0.0.9", source: "dns", last_seen: null },
        ],
      },
    };
    renderPage("roaming-laptop");
    expect(screen.getByText(/hostname resolves to 2 IPs/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /10\.0\.0\.5/ })).toHaveAttribute(
      "href",
      "/hosts/10.0.0.5",
    );
    expect(screen.getByText(/· no traffic/)).toBeInTheDocument();
  });
});
