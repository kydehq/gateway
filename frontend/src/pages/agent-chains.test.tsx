import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { DlpAlert, SessionDetail, SessionSummary } from "@/api/types";

const h = vi.hoisted(() => ({
  sessionsFilters: undefined as unknown,
  sessions: {
    data: undefined as { pages: unknown[] } | undefined,
    isLoading: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  },
  session: { data: undefined as SessionDetail | undefined, isLoading: false },
  dlpAlert: {
    data: undefined as DlpAlert | undefined,
    isLoading: false,
    isError: false,
  },
  me: { isAdmin: false, isAuditor: false },
  downloadPdf: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useSessionsInfinite: (filters: unknown) => {
    h.sessionsFilters = filters;
    return h.sessions;
  },
  useSession: () => h.session,
  useDlpAlert: () => h.dlpAlert,
  useEntry: () => ({ data: undefined, isLoading: false, error: null }),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  downloadPdf: (...args: unknown[]) => h.downloadPdf(...args),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("@/hooks/use-agent-label", () => ({
  useAgentLabel: () => ({ shortLabel: (s: string) => s, label: (s: string) => s }),
}));
vi.mock("sonner", () => ({ toast: h.toast }));

import AgentChainsPage from "./agent-chains";

function makeSession(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    session_id: "sess-1",
    serial_id: 1,
    entry_count: 3,
    agent_count: 1,
    first_time: "2026-07-01T10:00:00Z",
    last_time: "2026-07-01T10:05:00Z",
    agents: ["agent:a"],
    duration_seconds: 90,
    intent: "export_customer_data",
    status: "allowed",
    ...overrides,
  };
}

function setSessions(items: SessionSummary[]) {
  h.sessions.data = { pages: [{ items, has_more: false, next_cursor: null }] };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function renderPage(initial = "/agent-chains") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route
          path="/agent-chains"
          element={
            <>
              <AgentChainsPage />
              <LocationProbe />
            </>
          }
        />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.sessionsFilters = undefined;
  h.sessions = {
    data: undefined,
    isLoading: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  };
  h.session = { data: undefined, isLoading: false };
  h.dlpAlert = { data: undefined, isLoading: false, isError: false };
  h.me = { isAdmin: false, isAuditor: false };
  h.downloadPdf = vi.fn().mockResolvedValue(undefined);
  h.toast.success.mockReset();
  h.toast.error.mockReset();
  h.toast.info.mockReset();
});

describe("AgentChainsPage — list & filters", () => {
  it("shows skeletons while loading", () => {
    h.sessions.isLoading = true;
    const { container } = renderPage();
    expect(screen.getByText("Agent Chains")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state and defaults to the incidents filter", () => {
    setSessions([]);
    renderPage();
    expect(
      screen.getByText("No agent chains in the selected window."),
    ).toBeInTheDocument();
    expect(h.sessionsFilters).toEqual(
      expect.objectContaining({
        window: "30d",
        status: ["blocked", "observed"],
        agents: [],
      }),
    );
  });

  it("switches the backend status filter via the chips", async () => {
    setSessions([]);
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Allowed" }));
    expect(h.sessionsFilters).toEqual(
      expect.objectContaining({ status: ["allowed"] }),
    );
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(h.sessionsFilters).toEqual(expect.objectContaining({ status: [] }));
  });

  it("pre-filters by ?agent= with an all-time window and a clearable banner", async () => {
    setSessions([]);
    renderPage("/agent-chains?agent=agent%3Ax");
    expect(h.sessionsFilters).toEqual(
      expect.objectContaining({ window: "all", agents: ["agent:x"], status: [] }),
    );
    expect(screen.getByText(/Filtered to agent/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Clear/ }));
    expect(screen.queryByText(/Filtered to agent/)).not.toBeInTheDocument();
  });

  it("selects a chain from the recent list", async () => {
    setSessions([
      makeSession({ session_id: "sess-1", serial_id: 1, intent: "first_chain" }),
      makeSession({ session_id: "sess-2", serial_id: 2, intent: "second_chain" }),
    ]);
    renderPage();
    // Auto-selected first chain drives the banner.
    expect(screen.getByText(/first chain — Completed/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("second chain"));
    expect(screen.getByText(/second chain — Completed/)).toBeInTheDocument();
  });
});

describe("AgentChainsPage — chain detail", () => {
  const blockedDetail: SessionDetail = {
    session_id: "sess-1",
    entries: [
      {
        seq: 1,
        entry_id: "e1",
        dt: "2026-07-01T10:00:00Z",
        agent_id: "agent:a",
        action_type: "chat",
        model: "gpt-x",
        why_last: "[user] export the customer table",
      },
      {
        seq: 2,
        entry_id: "e2",
        dt: "2026-07-01T10:01:00Z",
        agent_id: "agent:a",
        action_type: "tool_call",
        model: "gpt-x",
        tool_calls: [{ function: "sql_query" }, { function: "csv_dump" }],
      },
      {
        seq: 3,
        entry_id: "e3",
        dt: "2026-07-01T10:02:00Z",
        agent_id: "agent:a",
        action_type: "policy_block",
        model: "gpt-x",
      },
    ],
  };

  it("derives the blocked outcome, KPIs, and step statuses", () => {
    setSessions([makeSession({ status: "blocked" })]);
    h.session = { data: blockedDetail, isLoading: false };
    renderPage();
    expect(
      screen.getByText(/export customer data — Blocked at step 3/),
    ).toBeInTheDocument();
    // Chain id shows in the banner and the recent list.
    expect(screen.getAllByText(/CHAIN-0001/).length).toBeGreaterThan(0);
    // Blocked-at-step KPI: 3 of 3.
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    // 90s duration → minutes.
    expect(screen.getByText("1.5 min")).toBeInTheDocument();
    // Step cards: chat, first tool name, policy block.
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.getByText("sql_query")).toBeInTheDocument();
    expect(screen.getByText("sql_query, csv_dump")).toBeInTheDocument();
    expect(screen.getByText("Policy Block")).toBeInTheDocument();
    expect(screen.getByText("BLOCKED", { selector: "span.mt-2" })).toBeInTheDocument();
  });

  it("opens the alert sheet from a flagged (PREVENTED) step", async () => {
    setSessions([makeSession({ status: "observed" })]);
    h.session = {
      isLoading: false,
      data: {
        session_id: "sess-1",
        entries: [
          {
            seq: 1,
            dt: "2026-07-01T10:00:00Z",
            agent_id: "agent:a",
            action_type: "chat",
            model: "gpt-x",
            dlp_alerts: [{ alert_id: "alt-1", serial_id: 7 }],
          },
        ],
      },
    };
    h.dlpAlert = {
      isLoading: false,
      isError: false,
      data: {
        id: 7,
        serial_id: 7,
        alert_id: "alt-1",
        created_dt: "2026-07-01T10:00:00Z",
        scanner: "regex",
        score: 0.8,
        status: "new",
      },
    };
    renderPage();
    expect(screen.getByText(/Alert raised \(1\)/)).toBeInTheDocument();
    expect(screen.getByText("1 alert raised")).toBeInTheDocument();
    await userEvent.click(screen.getByText(/FLAGGED — DLP alert raised/));
    await waitFor(() =>
      expect(screen.getByText("What Happened")).toBeInTheDocument(),
    );
    // Jump-out to the threats page.
    await userEvent.click(
      screen.getByRole("button", { name: "Open in Threats →" }),
    );
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
  });

  it("opens the entry-detail dialog for benign steps via ?entry=", async () => {
    setSessions([makeSession({})]);
    h.session = { data: blockedDetail, isLoading: false };
    renderPage();
    await userEvent.click(screen.getByText("Chat"));
    expect(screen.getByTestId("loc")).toHaveTextContent("entry=1");
  });

  it("shows the alert sheet loading state", async () => {
    setSessions([makeSession({ status: "observed" })]);
    h.session = {
      isLoading: false,
      data: {
        session_id: "sess-1",
        entries: [
          {
            seq: 1,
            dt: "2026-07-01T10:00:00Z",
            agent_id: "agent:a",
            action_type: "chat",
            model: "gpt-x",
            dlp_alerts: [{ alert_id: "alt-1", serial_id: 7 }],
          },
        ],
      },
    };
    h.dlpAlert = { data: undefined, isLoading: true, isError: false };
    renderPage();
    await userEvent.click(screen.getByText(/FLAGGED — DLP alert raised/));
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});

describe("AgentChainsPage — role actions", () => {
  beforeEach(() => {
    setSessions([makeSession({})]);
    h.session = { data: { session_id: "sess-1", entries: [] }, isLoading: false };
  });

  it("gives admins acknowledge / export / drill-down actions", async () => {
    h.me = { isAdmin: true, isAuditor: false };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(h.toast.success).toHaveBeenCalledWith("Chain acknowledged");
    await userEvent.click(screen.getByRole("button", { name: "Add to Policy" }));
    expect(h.toast.info).toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export for Incident Report" }),
    );
    await waitFor(() =>
      expect(h.downloadPdf).toHaveBeenCalledWith(
        "/api/export/incident-report",
        expect.objectContaining({
          status: "ALLOWED",
          incident_serial: "INC-0001",
        }),
        "incident-1.pdf",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Incident report downloaded");

    await userEvent.click(
      screen.getByRole("button", { name: "Show all chains from this agent →" }),
    );
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/agent-chains?agent=agent%3Aa",
    );
    expect(screen.getByText(/Filtered to agent/)).toBeInTheDocument();
  });

  it("surfaces incident-report export failures", async () => {
    h.me = { isAdmin: true, isAuditor: false };
    h.downloadPdf = vi.fn().mockRejectedValue(new Error("500 pdf"));
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export for Incident Report" }),
    );
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("500 pdf"));
  });

  it("gives auditors disposition, notes, and evidence export", async () => {
    renderPage();
    expect(screen.getByText("Disposition")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Add notes..."), "checked");
    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export as Compliance Evidence" }),
    );
    await waitFor(() => expect(h.downloadPdf).toHaveBeenCalled());
    await userEvent.click(
      screen.getByRole("button", { name: "Show full audit trail →" }),
    );
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
  });
});
