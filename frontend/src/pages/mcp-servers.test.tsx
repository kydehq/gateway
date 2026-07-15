import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { McpAggregatorCatalog, McpServer } from "@/api/types";

const h = vi.hoisted(() => ({
  servers: {
    data: undefined as McpServer[] | undefined,
    isLoading: false,
    isError: false,
    error: undefined as Error | undefined,
    dataUpdatedAt: 1,
  },
  catalog: { data: undefined as McpAggregatorCatalog | undefined },
  del: { mutateAsync: vi.fn(), isPending: false },
  me: { isAdmin: true },
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/api/queries", () => ({
  useMcpServers: () => h.servers,
  useDeleteMcpServer: () => h.del,
  useMcpAggregatorCatalog: () => h.catalog,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("sonner", () => ({ toast: h.toast }));
// The add/edit dialog and policy sheet have their own hooks + tests; stub
// them with markers so this page test can assert open/close wiring only.
vi.mock("@/components/shared/mcp-server-dialog", () => ({
  McpServerDialog: ({ open, server }: { open: boolean; server?: McpServer }) =>
    open ? <div>dialog:{server?.name ?? "new"}</div> : null,
}));
vi.mock("@/components/shared/mcp-policy-sheet", () => ({
  McpPolicySheet: ({
    open,
    server,
    readOnly,
  }: {
    open: boolean;
    server: McpServer | null;
    readOnly: boolean;
  }) => (open ? <div>{`sheet:${server?.name}:${readOnly}`}</div> : null),
}));

import McpServersPage from "./mcp-servers";

const writeText = vi.fn();

function server(overrides: Partial<McpServer>): McpServer {
  return {
    id: "1",
    name: "files",
    upstream_url: "http://mcp-files:9000",
    enabled: true,
    created_at: "2026-06-01T08:00:00Z",
    created_by: 1,
    last_call_at: null,
    last_error_at: null,
    last_error_status: null,
    last_error_snippet: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <McpServersPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Radix dropdown-menu touches pointer-capture APIs jsdom doesn't ship.
  const el = Element.prototype as unknown as Record<string, unknown>;
  el.hasPointerCapture ??= () => false;
  el.setPointerCapture ??= () => {};
  el.releasePointerCapture ??= () => {};
  el.scrollIntoView ??= () => {};

  h.servers = {
    data: [],
    isLoading: false,
    isError: false,
    error: undefined,
    dataUpdatedAt: 1,
  };
  h.catalog = { data: undefined };
  h.del = { mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
  h.me = { isAdmin: true };
  h.toast.success.mockReset();
  h.toast.error.mockReset();
  writeText.mockReset();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

describe("McpServersPage — table states", () => {
  it("shows skeleton rows while loading", () => {
    h.servers.isLoading = true;
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the error row", () => {
    h.servers = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("502 bad gateway"),
      dataUpdatedAt: 0,
    };
    renderPage();
    expect(screen.getByText("Failed to load: 502 bad gateway")).toBeInTheDocument();
  });

  it("shows the empty state", () => {
    renderPage();
    expect(screen.getByText(/No MCP servers registered yet/)).toBeInTheDocument();
  });

  it("renders rows with status chips and error recency", () => {
    h.servers.data = [
      server({
        id: "1",
        name: "files",
        last_call_at: "2026-07-15T09:00:00Z",
        // Fresh error → red chip with the status code.
        last_error_at: new Date().toISOString(),
        last_error_status: 502,
        last_error_snippet: "upstream reset",
      }),
      server({ id: "2", name: "tickets", enabled: false }),
    ];
    renderPage();
    expect(screen.getByText("files")).toBeInTheDocument();
    expect(screen.getByText(/\/mcp\/files$/)).toBeInTheDocument();
    expect(screen.getByText("enabled")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.getByText("502")).toBeInTheDocument();
    expect(screen.getByTitle("upstream reset")).toBeInTheDocument();
  });

  it("summarises the aggregator catalog once loaded", () => {
    h.catalog = {
      data: { items: [], server_count: 2, tool_count: 12, oldest_seconds: 120 },
    };
    renderPage();
    expect(
      screen.getByText("12 tools across 2 servers · oldest entry 2m ago"),
    ).toBeInTheDocument();
  });

  it("shows the catalog loading note until it arrives", () => {
    renderPage();
    expect(screen.getByText("Loading catalog…")).toBeInTheDocument();
  });
});

describe("McpServersPage — actions", () => {
  it("copies the aggregator and per-server gateway URLs", async () => {
    h.servers.data = [server({})];
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "Copy aggregator URL" }),
    );
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/mcp`);
    expect(h.toast.success).toHaveBeenCalledWith("Aggregator URL copied");

    await userEvent.click(screen.getByRole("button", { name: "Copy gateway URL" }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/mcp/files`);
    expect(h.toast.success).toHaveBeenCalledWith("Gateway URL copied");
  });

  it("opens the add dialog from the header button", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Add MCP server/ }));
    expect(screen.getByText("dialog:new")).toBeInTheDocument();
  });

  it("opens the edit dialog and policy sheet from the row menu", async () => {
    h.servers.data = [server({})];
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Edit"));
    expect(screen.getByText("dialog:files")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Policies…"));
    expect(screen.getByText("sheet:files:false")).toBeInTheDocument();
  });

  it("deletes a server after confirmation", async () => {
    h.servers.data = [server({})];
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Delete"));
    expect(screen.getByText("Remove files?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(h.del.mutateAsync).toHaveBeenCalledWith("files"));
    expect(h.toast.success).toHaveBeenCalledWith("Removed files");
  });

  it("cancels a delete without calling the API", async () => {
    h.servers.data = [server({})];
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Delete"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(h.del.mutateAsync).not.toHaveBeenCalled();
  });

  it("surfaces delete failures", async () => {
    h.servers.data = [server({})];
    h.del.mutateAsync = vi.fn().mockRejectedValue(new Error("409 has policies"));
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Delete"));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(h.toast.error).toHaveBeenCalledWith("409 has policies"),
    );
  });

  it("shows the read-only view and passes readOnly to the policy sheet for auditors", async () => {
    h.me = { isAdmin: false };
    h.servers.data = [server({})];
    renderPage();
    expect(
      screen.queryByRole("button", { name: /Add MCP server/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "MCP server actions" }),
    );
    await userEvent.click(await screen.findByText("Policies…"));
    expect(screen.getByText("sheet:files:true")).toBeInTheDocument();
  });
});

describe("McpServersPage — sorting", () => {
  it("flips the name sort direction", async () => {
    h.servers.data = [
      server({ id: "1", name: "alpha" }),
      server({ id: "2", name: "zeta" }),
    ];
    renderPage();
    const names = () =>
      screen
        .getAllByRole("row")
        .slice(1)
        .map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(names()[0]).toContain("alpha");
    await userEvent.click(screen.getByText("Name"));
    expect(names()[0]).toContain("zeta");
  });
});
