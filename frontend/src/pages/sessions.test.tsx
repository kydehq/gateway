import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
  session: {
    data: undefined as SessionDetail | undefined,
    isLoading: false,
    isError: false,
    error: null as Error | null,
  },
  dlpAlert: { data: undefined as DlpAlert | undefined },
  alerts: { data: [] as DlpAlert[], isLoading: false },
  downloadPdf: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  // Keep SESSION_SORTS / STATS_WINDOWS and the other constants real.
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useSessionsInfinite: (filters: unknown) => {
    h.sessionsFilters = filters;
    return h.sessions;
  },
  useSession: () => h.session,
  useDlpAlert: () => h.dlpAlert,
  useDlpAlerts: () => h.alerts,
  useEntry: () => ({ data: undefined, isLoading: false, error: null }),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  downloadPdf: (...args: unknown[]) => h.downloadPdf(...args),
}));
vi.mock("@/hooks/use-me", () => ({
  useMe: () => ({ me: undefined, isAdmin: false, isAuditor: false }),
}));
vi.mock("sonner", () => ({ toast: h.toast }));

import SessionsPage from "./sessions";

function makeSession(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    session_id: "sess-1",
    serial_id: 1,
    entry_count: 4,
    agent_count: 1,
    first_time: "2026-07-01T10:00:00Z",
    last_time: "2026-07-01T10:05:00Z",
    agents: ["agent:a"],
    ...overrides,
  };
}

function setSessions(items: SessionSummary[]) {
  h.sessions.data = { pages: [{ items, has_more: false, next_cursor: null }] };
}

function renderPage(initial = "/sessions") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/sessions/:sessionId" element={<SessionsPage />} />
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
  h.session = { data: undefined, isLoading: false, isError: false, error: null };
  h.dlpAlert = { data: undefined };
  h.alerts = { data: [], isLoading: false };
  h.downloadPdf = vi.fn().mockResolvedValue(undefined);
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("SessionsPage — list", () => {
  it("shows skeletons while the first page loads", () => {
    h.sessions.isLoading = true;
    const { container } = renderPage();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state and the select-a-session hint", () => {
    setSessions([]);
    renderPage();
    expect(screen.getByText("No sessions yet.")).toBeInTheDocument();
    expect(
      screen.getByText("Select a session to view its entries."),
    ).toBeInTheDocument();
  });

  it("requests the default filters", () => {
    setSessions([]);
    renderPage();
    expect(h.sessionsFilters).toEqual({
      window: "24h",
      has_alert: "any",
      agents: [],
      sort: "newest",
      status: [],
    });
  });

  it("lists sessions with serial ids, entry counts, and open-alert badges", () => {
    setSessions([
      makeSession({ session_id: "sess-1", serial_id: 1 }),
      makeSession({ session_id: "sess-2", serial_id: 2, entry_count: 9 }),
    ]);
    h.alerts = {
      isLoading: false,
      data: [
        // Two open + one closed on sess-2 → badge shows 2.
        { id: 1, session_id: "sess-2", status: "new" } as DlpAlert,
        { id: 2, session_id: "sess-2", status: "in_review" } as DlpAlert,
        { id: 3, session_id: "sess-2", status: "closed" } as DlpAlert,
      ],
    };
    renderPage("/sessions/sess-1");
    expect(screen.getByText("SES-0001")).toBeInTheDocument();
    expect(screen.getByText("SES-0002")).toBeInTheDocument();
    expect(screen.getByText("9 entries")).toBeInTheDocument();
    expect(screen.getByText("⚠ 2")).toBeInTheDocument();
  });

  it("auto-selects the first session when none is routed", async () => {
    setSessions([makeSession({ session_id: "sess-9", serial_id: 9 })]);
    h.session = {
      data: { session_id: "sess-9", entries: [] },
      isLoading: false,
      isError: false,
      error: null,
    };
    renderPage("/sessions");
    await waitFor(() =>
      expect(screen.getByText("SESSION: sess-9")).toBeInTheDocument(),
    );
  });

  it("filters the list by the search box", async () => {
    setSessions([
      makeSession({ session_id: "sess-abc", serial_id: 1 }),
      makeSession({ session_id: "sess-xyz", serial_id: 2 }),
    ]);
    renderPage("/sessions/sess-abc");
    await userEvent.type(screen.getByPlaceholderText("Filter sessions…"), "xyz");
    expect(screen.queryByText("SES-0001")).not.toBeInTheDocument();
    expect(screen.getByText("SES-0002")).toBeInTheDocument();
    await userEvent.type(
      screen.getByPlaceholderText("Filter sessions…"),
      "-no-match",
    );
    expect(screen.getByText("No sessions match.")).toBeInTheDocument();
  });
});

describe("SessionsPage — detail panel", () => {
  const detail: SessionDetail = {
    session_id: "sess-1",
    hosts: [
      { ip: "10.0.0.5", hostname: "build-01" },
      { ip: "10.0.0.6", hostname: null },
    ],
    entries: [
      {
        seq: 1,
        dt: "2026-07-01T10:00:00Z",
        agent_id: "agent:a",
        action_type: "chat",
        model: "gpt-x",
        why_last: "[user] please summarise the report",
      },
      {
        seq: 2,
        dt: "2026-07-01T10:01:00Z",
        agent_id: "agent:b",
        action_type: "tool_call",
        model: "gpt-x",
        why_last: "[assistant] calling tools",
        tool_count: 2,
        tool_calls: [{ function: "read_file" }, { function: "write_file" }],
        dlp_alerts: [{ alert_id: "alt-1", serial_id: 7, severity: "high" }],
      },
      {
        seq: 3,
        dt: "2026-07-01T10:02:00Z",
        agent_id: "agent:a",
        action_type: "api_call",
        model: "embed-1",
        request_kind: "embedding",
        prompt_tokens: 12,
        completion_tokens: 0,
      },
    ],
  };

  function renderDetail() {
    setSessions([makeSession({ session_id: "sess-1" })]);
    h.session = { data: detail, isLoading: false, isError: false, error: null };
    return renderPage("/sessions/sess-1");
  }

  it("shows loading skeletons for the panel", () => {
    setSessions([makeSession({})]);
    h.session = { data: undefined, isLoading: true, isError: false, error: null };
    const { container } = renderPage("/sessions/sess-1");
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("surfaces session load failures", () => {
    setSessions([makeSession({})]);
    h.session = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("500 upstream"),
    };
    renderPage("/sessions/sess-1");
    expect(
      screen.getByText("Failed to load session: 500 upstream"),
    ).toBeInTheDocument();
  });

  it("renders metrics, agent chips, host chips, and the entry timeline", () => {
    renderDetail();
    // Two distinct agents across three entries.
    expect(screen.getByText("Agents:")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "agent:a" })).toHaveAttribute(
      "href",
      "/agents/agent%3Aa",
    );
    // Hosts render "hostname (ip)" when labeled, bare ip otherwise.
    expect(screen.getByText("Hosts:")).toBeInTheDocument();
    expect(screen.getByTitle("Open host build-01")).toHaveAttribute(
      "href",
      "/hosts/10.0.0.5",
    );
    expect(screen.getByText("10.0.0.6")).toBeInTheDocument();
    // Chat entry body with role chip.
    expect(screen.getByText("please summarise the report")).toBeInTheDocument();
    expect(screen.getByText("USER")).toBeInTheDocument();
    // Tool entry lists its tool calls.
    expect(screen.getByText("read_file, write_file")).toBeInTheDocument();
    expect(screen.getByText("TOOL CALL")).toBeInTheDocument();
    // Non-chat entry synthesises a kind line instead of a body.
    expect(screen.getByText("EMBEDDING")).toBeInTheDocument();
    expect(screen.getByText(/12 → 0 tokens/)).toBeInTheDocument();
  });

  it("expands and collapses long messages in place", async () => {
    const long = "x".repeat(300);
    setSessions([makeSession({ session_id: "sess-1" })]);
    h.session = {
      isLoading: false,
      isError: false,
      error: null,
      data: {
        session_id: "sess-1",
        entries: [
          {
            seq: 1,
            dt: "2026-07-01T10:00:00Z",
            agent_id: "agent:a",
            action_type: "chat",
            model: "gpt-x",
            why_last: `[user] ${long}`,
          },
        ],
      },
    };
    renderPage("/sessions/sess-1");
    const btn = screen.getByRole("button", { name: "show more" });
    expect(screen.queryByText(long)).not.toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.getByText(long)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "show less" }));
    expect(screen.queryByText(long)).not.toBeInTheDocument();
  });

  it("opens the DLP alert sheet from an entry's alert chip", async () => {
    h.dlpAlert = {
      data: {
        id: 7,
        serial_id: 7,
        alert_id: "alt-1",
        created_dt: "2026-07-01T10:01:00Z",
        scanner: "regex",
        score: 0.8,
        status: "new",
      },
    };
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /ALT-0007/ }));
    await waitFor(() =>
      expect(screen.getByText("What Happened")).toBeInTheDocument(),
    );
  });

  it("exports session evidence as PDF", async () => {
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: "🛡 Export Evidence" }));
    await waitFor(() =>
      expect(h.downloadPdf).toHaveBeenCalledWith(
        "/api/export/compliance-evidence",
        { kind: "session", id: "sess-1" },
        "session-sess-1.pdf",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Evidence downloaded");
  });

  it("toasts when the evidence export fails", async () => {
    h.downloadPdf = vi.fn().mockRejectedValue(new Error("503 export"));
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: "🛡 Export Evidence" }));
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("503 export"));
  });

  it("links to the session's full audit trail", async () => {
    renderDetail();
    await userEvent.click(
      screen.getByRole("button", { name: "Full audit trail →" }),
    );
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
  });
});
