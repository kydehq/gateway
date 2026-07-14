import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({
  servers: { data: undefined as unknown, isLoading: true, isError: false, error: undefined as unknown, dataUpdatedAt: 0 },
  catalog: { data: undefined as unknown, isLoading: false },
  me: { isAdmin: true },
}));
vi.mock("@/api/queries", () => ({
  useMcpServers: () => h.servers,
  useDeleteMcpServer: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMcpAggregatorCatalog: () => h.catalog,
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
// The add/edit dialog has its own hooks + tests; stub it so this page test
// doesn't have to mock its query surface too.
vi.mock("@/components/shared/mcp-server-dialog", () => ({
  McpServerDialog: () => null,
}));
vi.mock("@/components/shared/mcp-policy-sheet", () => ({
  McpPolicySheet: () => null,
}));

import McpServersPage from "./mcp-servers";

function renderPage() {
  return render(
    <MemoryRouter>
      <McpServersPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.servers = { data: undefined, isLoading: true, isError: false, error: undefined, dataUpdatedAt: 0 };
  h.catalog = { data: undefined, isLoading: false };
  h.me = { isAdmin: true };
});

describe("McpServersPage", () => {
  it("renders the header while loading", () => {
    renderPage();
    expect(screen.getByText("MCP Servers")).toBeInTheDocument();
  });

  it("renders the header in the empty state", () => {
    h.servers = { data: [], isLoading: false, isError: false, error: undefined, dataUpdatedAt: 1 };
    renderPage();
    expect(screen.getByText("MCP Servers")).toBeInTheDocument();
  });
});
