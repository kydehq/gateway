import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { DlpAlert, EntriesPage, EntryFacets, EntryRow, Verify } from "@/api/types";

const h = vi.hoisted(() => ({
  entries: {
    data: undefined as { pages: EntriesPage[] } | undefined,
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  },
  entriesParams: undefined as Record<string, unknown> | undefined,
  facets: { data: undefined as EntryFacets | undefined },
  verify: { data: undefined as Verify | undefined },
  alerts: { data: [] as DlpAlert[] },
  sessions: {
    data: undefined as
      | { pages: Array<{ items: Array<{ session_id: string; serial_id?: number }> }> }
      | undefined,
  },
  features: { signingEnabled: true },
  downloadPdf: vi.fn(),
  downloadFile: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useEntriesInfinite: (params: Record<string, unknown>) => {
    h.entriesParams = params;
    return h.entries;
  },
  useEntryFacets: () => h.facets,
  useVerify: () => h.verify,
  useDlpAlerts: () => h.alerts,
  useSessionsInfinite: () => h.sessions,
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  downloadPdf: (...args: unknown[]) => h.downloadPdf(...args),
  downloadFile: (...args: unknown[]) => h.downloadFile(...args),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("@/hooks/use-agent-label", () => ({
  useAgentLabel: () => ({ shortLabel: (s: string) => (s ? `short(${s})` : "—") }),
}));
vi.mock("sonner", () => ({ toast: h.toast }));

import AuditLogPage from "./audit-log";

function entry(overrides: Partial<EntryRow>): EntryRow {
  return {
    seq: 5,
    dt: "2026-07-01T12:00:00Z",
    agent_id: "agent:a",
    action_type: "chat",
    model: "gpt-x",
    upstream: "api.openai.com",
    prompt_tokens: 1500,
    completion_tokens: 200,
    session_id: "sess-1",
    tool_count: 0,
    entry_id: "e1",
    ...overrides,
  };
}

function page(items: EntryRow[], total?: number): { pages: EntriesPage[] } {
  return {
    pages: [{ items, next_cursor: null, has_more: false, total_count: total }],
  };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>;
}

function renderPage(initialEntry = "/audit") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/audit"
          element={
            <>
              <AuditLogPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.entries = {
    data: page([]),
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
  h.entriesParams = undefined;
  h.facets = { data: { action_types: ["chat", "tool"], upstreams: ["api.openai.com"] } };
  h.verify = {
    data: {
      valid: true,
      entry_count: 100,
      chain_breaks: 0,
      signature_failures: 0,
      errors: [],
    } as Verify,
  };
  h.alerts = { data: [] };
  h.sessions = { data: { pages: [{ items: [{ session_id: "sess-1", serial_id: 7 }] }] } };
  h.features = { signingEnabled: true };
  h.downloadPdf = vi.fn().mockResolvedValue(undefined);
  h.downloadFile = vi.fn().mockResolvedValue(undefined);
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("AuditLogPage — states and KPIs", () => {
  it("shows skeletons while loading", () => {
    h.entries.data = undefined;
    h.entries.isLoading = true;
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state", () => {
    renderPage();
    expect(screen.getByText("No entries match.")).toBeInTheDocument();
  });

  it("renders verification KPIs and the total with a Showing subtext", () => {
    h.entries.data = page([entry({})], 250);
    renderPage();
    expect(screen.getByText("250")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 250")).toBeInTheDocument();
    expect(screen.getByText("VERIFIED")).toBeInTheDocument();
    // Signature failures KPI shows the zero.
    expect(screen.getByText("Signature Failures")).toBeInTheDocument();
  });

  it("shows BROKEN when the chain fails verification", () => {
    h.verify = {
      data: {
        valid: false,
        entry_count: 100,
        chain_breaks: 1,
        signature_failures: 3,
        errors: [],
      } as Verify,
    };
    renderPage();
    expect(screen.getByText("BROKEN")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("locks the signing KPIs in the sandbox edition", () => {
    h.features = { signingEnabled: false };
    renderPage();
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
    expect(screen.getAllByText("Enterprise only").length).toBe(2);
  });

  it("computes the date range across loaded entries", () => {
    h.entries.data = page([
      entry({ seq: 1, dt: "2026-07-01T12:00:00Z" }),
      entry({ seq: 2, dt: "2026-07-03T12:00:00Z", entry_id: "e2", session_id: "" }),
    ]);
    renderPage();
    expect(screen.getByText(/1\.7\.2026 – 3\.7\.2026/)).toBeInTheDocument();
  });
});

describe("AuditLogPage — table rows", () => {
  it("renders seq, agent link, session serial, provider, tokens, and alert badge", () => {
    h.alerts = {
      data: [{ entry_id: "e1", serial_id: 9, id: "uuid-9" } as unknown as DlpAlert],
    };
    h.entries.data = page([
      entry({}),
      entry({ seq: 6, entry_id: "e2", session_id: "sess-unknown", model: "" }),
    ]);
    renderPage();
    expect(screen.getByText("SEQ-5")).toBeInTheDocument();
    // Agent cell uses the derived short label; raw id is on the title.
    expect(screen.getAllByTitle("Open agent agent:a").length).toBe(2);
    expect(screen.getAllByText("short(agent:a)").length).toBe(2);
    // Session serial resolves from the sessions lookup, or falls back.
    expect(screen.getByRole("link", { name: "SES-0007" })).toHaveAttribute(
      "href",
      "/sessions/sess-1",
    );
    expect(screen.getByText("SES-?")).toBeInTheDocument();
    // Provider names are rewritten for readability.
    expect(screen.getAllByText("OpenAI").length).toBe(2);
    // Token formatting.
    expect(screen.getAllByText("1.5K").length).toBe(2);
    expect(screen.getAllByText("200").length).toBe(2);
    // Alert badge on the first row only.
    expect(screen.getByText("⚠ 1")).toHaveAttribute("title", "ALT-0009");
  });

  it("opens the entry dialog via ?entry= on row click", async () => {
    h.entries.data = page([entry({})]);
    renderPage();
    await userEvent.click(screen.getByText("SEQ-5"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/audit?entry=5");
  });

  it("shows the loading-more indicator while fetching the next page", () => {
    h.entries.data = page([entry({})]);
    h.entries.isFetchingNextPage = true;
    renderPage();
    expect(screen.getByText("Loading more…")).toBeInTheDocument();
  });
});

describe("AuditLogPage — filters", () => {
  it("shows the inbound session banner, widens the window, and clears it", async () => {
    renderPage("/audit?session=sess-1");
    expect(screen.getByText(/Filtered to session/)).toBeInTheDocument();
    expect(screen.getByText("sess-1")).toBeInTheDocument();
    // A pre-filtered deep link widens the window so the target is findable.
    expect(h.entriesParams).toMatchObject({ session_id: "sess-1", window: "all" });
    // The banner's Clear strips the URL param.
    await userEvent.click(screen.getAllByRole("button", { name: /Clear/ })[1]);
    expect(screen.getByTestId("loc")).toHaveTextContent(/^\/audit$/);
  });

  it("shows the agent banner for ?agent= deep links", () => {
    renderPage("/audit?agent=agent:a");
    expect(screen.getByText(/Filtered to agent/)).toBeInTheDocument();
    expect(h.entriesParams).toMatchObject({ agent_id: "agent:a" });
  });

  it("passes the debounced search to the query and clears all filters", async () => {
    renderPage();
    const input = screen.getByPlaceholderText("Search entries… (/)");
    await userEvent.type(input, "leak");
    await waitFor(() => expect(h.entriesParams).toMatchObject({ q: "leak" }));
    await userEvent.click(screen.getByRole("button", { name: /Clear/ }));
    await waitFor(() =>
      expect(h.entriesParams).toMatchObject({ q: undefined, window: "24h" }),
    );
    expect(input).toHaveValue("");
  });

  it("focuses the search input on '/'", async () => {
    renderPage();
    await userEvent.keyboard("/");
    expect(screen.getByPlaceholderText("Search entries… (/)")).toHaveFocus();
  });
});

describe("AuditLogPage — exports", () => {
  it("exports the CSV with the active filters", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    await waitFor(() =>
      expect(h.downloadFile).toHaveBeenCalledWith(
        "/api/export/audit-log-csv",
        { window: "24h", limit: 5000 },
        "audit-log.csv",
        "text/csv",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Audit log CSV downloaded");
  });

  it("exports the PDF", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Export PDF/ }));
    await waitFor(() =>
      expect(h.downloadPdf).toHaveBeenCalledWith(
        "/api/export/audit-log",
        { window: "24h", limit: 500 },
        "audit-log.pdf",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Audit log downloaded");
  });

  it("surfaces export failures as an error toast", async () => {
    h.downloadFile = vi.fn().mockRejectedValue(new Error("503 export"));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("503 export"));
  });
});
