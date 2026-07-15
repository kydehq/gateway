import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { DlpAlert } from "@/api/types";

const h = vi.hoisted(() => ({
  alerts: { data: [] as DlpAlert[], isLoading: false },
  alertsSource: "unset" as unknown,
  deepLink: { data: undefined as DlpAlert | undefined, error: null as Error | null },
  transition: {
    // The page passes { onSuccess } to mutate; fire it so the success
    // toast + sheet-close paths run.
    mutate: vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    ),
    mutateAsync: vi.fn(),
    isPending: false,
  },
  blockAgent: { mutateAsync: vi.fn(), isPending: false },
  togglePolicy: { mutateAsync: vi.fn(), isPending: false },
  me: {
    me: { user_id: "7" } as unknown,
    isAdmin: false,
    isAuditor: false,
  },
  features: { enforcementEnabled: true },
  downloadPdf: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("@/api/queries", () => ({
  useDlpAlerts: (src?: unknown) => {
    h.alertsSource = src;
    return h.alerts;
  },
  useDlpAlert: () => h.deepLink,
  useTransitionDlpAlert: () => h.transition,
  useBlockAgent: () => h.blockAgent,
  useTogglePolicy: () => h.togglePolicy,
  useAgents: () => ({ data: [] }),
  useEntry: () => ({ data: undefined, isLoading: false, error: null }),
  useDlpAlertEvents: () => ({ data: [], isLoading: false, isError: false }),
  useCreateDlpRule: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  downloadPdf: (...args: unknown[]) => h.downloadPdf(...args),
}));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("sonner", () => ({ toast: h.toast }));

import ThreatsAlertsPage from "./threats-alerts";

let alertSeq = 0;
function makeAlert(overrides: Partial<DlpAlert>): DlpAlert {
  alertSeq += 1;
  return {
    id: alertSeq,
    serial_id: alertSeq,
    alert_id: `alt-${alertSeq}`,
    created_dt: "2026-07-01T10:00:00Z",
    scanner: "regex",
    score: 0.8,
    status: "new",
    ...overrides,
  };
}

function renderPage(initial = "/threats-alerts") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/threats-alerts" element={<ThreatsAlertsPage />} />
        <Route path="/alerts/:alertId" element={<ThreatsAlertsPage />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  alertSeq = 0;
  h.alerts = { data: [], isLoading: false };
  h.alertsSource = "unset";
  h.deepLink = { data: undefined, error: null };
  h.transition.mutate = vi.fn((_v: unknown, opts?: { onSuccess?: () => void }) =>
    opts?.onSuccess?.(),
  );
  h.transition.mutateAsync = vi.fn().mockResolvedValue({});
  h.transition.isPending = false;
  h.blockAgent.mutateAsync = vi.fn().mockResolvedValue({});
  h.togglePolicy.mutateAsync = vi.fn().mockResolvedValue({});
  h.me = { me: { user_id: "7" }, isAdmin: false, isAuditor: false };
  h.features = { enforcementEnabled: true };
  h.downloadPdf = vi.fn().mockResolvedValue(undefined);
  h.toast.success.mockReset();
  h.toast.error.mockReset();
  h.toast.warning.mockReset();
});

describe("ThreatsAlertsPage — list", () => {
  it("renders skeletons while loading", () => {
    h.alerts = { data: [], isLoading: true };
    const { container } = renderPage();
    expect(screen.getByText("Threats & Alerts")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the no-alerts-yet empty state", () => {
    renderPage();
    expect(screen.getByText("No alerts yet.")).toBeInTheDocument();
  });

  it("shows per-filter empty states when alerts exist but none match", async () => {
    h.alerts = { data: [makeAlert({ status: "new" })], isLoading: false };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "In Review" }));
    expect(screen.getByText("Nothing in review.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Escalated" }));
    expect(screen.getByText("Nothing escalated.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Closed" }));
    expect(screen.getByText("No closed alerts yet.")).toBeInTheDocument();
  });

  it("shows the all-triaged empty state when every alert is closed", () => {
    h.alerts = { data: [makeAlert({ status: "closed" })], isLoading: false };
    renderPage();
    expect(screen.getByText("No open alerts.")).toBeInTheDocument();
  });

  it("counts severities excluding closed alerts and renders rows with badges", () => {
    h.alerts = {
      isLoading: false,
      data: [
        makeAlert({ score: 0.95, prevented: true }),
        makeAlert({
          score: 0.75,
          source_type: "mcp",
          mcp_server_name: "files",
          mcp_tool_name: "read",
        }),
        makeAlert({ score: 0.95, status: "closed" }),
      ],
    };
    renderPage();
    // One open CRITICAL — the closed critical alert doesn't count.
    const critCard = screen.getByText("CRITICAL", { selector: ".eyebrow" }).closest("div")!.parentElement!;
    expect(critCard.querySelector(".stat-value")?.textContent).toBe("1");
    expect(screen.getByText("ALT-0001")).toBeInTheDocument();
    expect(screen.getByText("Prevented")).toBeInTheDocument();
    expect(screen.getByText("MCP · files · read")).toBeInTheDocument();
    // Closed row hidden by the default "Open" filter.
    expect(screen.queryByText("ALT-0003")).not.toBeInTheDocument();
  });

  it("passes the source filter through to the alerts query", async () => {
    renderPage();
    expect(h.alertsSource).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "MCP" }));
    expect(h.alertsSource).toBe("mcp");
    await userEvent.click(screen.getByRole("button", { name: "All sources" }));
    expect(h.alertsSource).toBeNull();
  });

  it("navigates to agent-chains from the row action", async () => {
    h.alerts = { data: [makeAlert({ agent_id: "agent:x" })], isLoading: false };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Show chain →" }));
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
  });
});

describe("ThreatsAlertsPage — triage sheet", () => {
  async function openDetails() {
    await userEvent.click(screen.getByRole("button", { name: "Details →" }));
  }

  it("opens the detail sheet and runs the start-review transition", async () => {
    h.alerts = { data: [makeAlert({ status: "new" })], isLoading: false };
    renderPage();
    await openDetails();
    expect(screen.getByText("What Happened")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start Review" }));
    expect(h.transition.mutate).toHaveBeenCalledWith(
      { alert_id: "alt-1", to_status: "in_review", disposition: undefined, note: undefined },
      expect.anything(),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Status updated");
    // onSuccess closes the sheet.
    await waitFor(() =>
      expect(screen.queryByText("What Happened")).not.toBeInTheDocument(),
    );
  });

  it("closes with a disposition via False Positive / Confirm Incident", async () => {
    h.alerts = { data: [makeAlert({ status: "in_review" })], isLoading: false };
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: "False Positive" }));
    expect(h.transition.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_status: "closed", disposition: "false_positive" }),
      expect.anything(),
    );
  });

  it("requires a reason before escalating, then records it", async () => {
    h.alerts = { data: [makeAlert({ status: "new" })], isLoading: false };
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: "Escalate" }));
    expect(screen.getByText("Escalate alert")).toBeInTheDocument();
    // Two "Escalate" buttons exist now (sheet action + dialog confirm);
    // the dialog confirm is the last one and starts disabled without a reason.
    const confirmBtn = () => {
      const btns = screen.getAllByRole("button", { name: "Escalate" });
      return btns[btns.length - 1];
    };
    expect(confirmBtn()).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason"), "possible exfil");
    await userEvent.click(confirmBtn());
    expect(h.transition.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_status: "escalated", note: "possible exfil" }),
      expect.anything(),
    );
  });

  it("offers de-escalation for escalated alerts", async () => {
    h.alerts = { data: [makeAlert({ status: "escalated" })], isLoading: false };
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: "De-escalate" }));
    expect(h.transition.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ to_status: "in_review" }),
      expect.anything(),
    );
  });

  it("lets admins disable a noisy regex pattern after confirmation", async () => {
    h.me = { me: { user_id: "7" }, isAdmin: true, isAuditor: false };
    h.alerts = {
      isLoading: false,
      data: [
        makeAlert({
          findings_parsed: [
            { pattern_id: "email-1", pattern_name: "Email", entity_type: "EMAIL" },
          ] as never,
        }),
      ],
    };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: 'Disable "Email"' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() =>
      expect(h.togglePolicy.mutateAsync).toHaveBeenCalledWith({
        id: "email-1",
        enabled: false,
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Disabled Email"),
    );
  });

  it("does not disable the pattern when the admin cancels the confirm", async () => {
    h.me = { me: { user_id: "7" }, isAdmin: true, isAuditor: false };
    h.alerts = {
      isLoading: false,
      data: [
        makeAlert({
          findings_parsed: [{ pattern_id: "email-1", pattern_name: "Email" }] as never,
        }),
      ],
    };
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: 'Disable "Email"' }));
    expect(h.togglePolicy.mutateAsync).not.toHaveBeenCalled();
  });

  it("lists each pattern when an alert carries several", async () => {
    h.me = { me: { user_id: "7" }, isAdmin: true, isAuditor: false };
    h.alerts = {
      isLoading: false,
      data: [
        makeAlert({
          findings_parsed: [
            { pattern_id: "email-1", pattern_name: "Email" },
            { pattern_id: "iban-1", pattern_name: "IBAN" },
            { pattern_id: "email-1", pattern_name: "Email" },
          ] as never,
        }),
      ],
    };
    renderPage();
    await openDetails();
    expect(screen.getByText("Disable a policy")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Email" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "IBAN" })).toBeInTheDocument();
  });

  it("lets admins block the agent behind an alert", async () => {
    h.me = { me: { user_id: "7" }, isAdmin: true, isAuditor: false };
    h.alerts = {
      isLoading: false,
      data: [makeAlert({ agent_id: "agent:x", findings_parsed: [] as never })],
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: "Block Agent" }));
    await waitFor(() =>
      expect(h.blockAgent.mutateAsync).toHaveBeenCalledWith({
        agent_id: "agent:x",
        reason: "Blocked from alert alt-1",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Agent agent:x blocked");
  });

  it("disables Add to Policy when the alert has no findings", async () => {
    h.me = { me: { user_id: "7" }, isAdmin: true, isAuditor: false };
    h.alerts = { data: [makeAlert({})], isLoading: false };
    renderPage();
    await openDetails();
    expect(screen.getByRole("button", { name: "Add to Policy" })).toBeDisabled();
  });

  it("gives non-admins the notes field and PDF evidence export", async () => {
    h.alerts = { data: [makeAlert({})], isLoading: false };
    renderPage();
    await openDetails();
    await userEvent.type(
      screen.getByPlaceholderText("Add notes for the audit record..."),
      "checked",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export as Evidence" }),
    );
    await waitFor(() =>
      expect(h.downloadPdf).toHaveBeenCalledWith(
        "/api/export/compliance-evidence",
        { kind: "alert", id: "alt-1" },
        "alert-alt-1.pdf",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Evidence downloaded");
  });

  it("shows the MCP source panel for MCP alerts", async () => {
    h.alerts = {
      isLoading: false,
      data: [
        makeAlert({
          source_type: "mcp",
          mcp_server_name: "files",
          mcp_method: "tools/call",
          mcp_tool_name: "read",
        }),
      ],
    };
    renderPage();
    await openDetails();
    expect(screen.getByText("MCP source")).toBeInTheDocument();
    expect(screen.getByText("tools/call")).toBeInTheDocument();
  });
});

describe("ThreatsAlertsPage — bulk actions", () => {
  function twoAlerts() {
    h.alerts = { data: [makeAlert({}), makeAlert({})], isLoading: false };
  }

  async function selectAll() {
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Select all visible alerts" }),
    );
  }

  it("select-all drives the toolbar and clears again", async () => {
    twoAlerts();
    renderPage();
    await selectAll();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText("2 selected")).not.toBeInTheDocument();
  });

  it("bulk-closes selected alerts as false positives", async () => {
    twoAlerts();
    renderPage();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select alert alt-1" }));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Close as False Positive" }),
    );
    expect(
      screen.getByText("Close as false positive — 1 alert?"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(h.transition.mutateAsync).toHaveBeenCalledWith({
        alert_id: "alt-1",
        to_status: "closed",
        disposition: "false_positive",
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith(
      "Close as false positive: 1 alert",
    );
  });

  it("bulk-assigns to the current user", async () => {
    twoAlerts();
    renderPage();
    await selectAll();
    await userEvent.click(screen.getByRole("button", { name: "Assign to me" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(h.transition.mutateAsync).toHaveBeenCalledWith({
        alert_id: "alt-2",
        to_status: "in_review",
        assignee_id: 7,
      }),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Assign to me: 2 alerts");
  });

  it("bulk-exports one PDF per selected alert", async () => {
    twoAlerts();
    renderPage();
    await selectAll();
    await userEvent.click(screen.getByRole("button", { name: "🛡 Export PDFs" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(h.downloadPdf).toHaveBeenCalledTimes(2));
    expect(h.downloadPdf).toHaveBeenCalledWith(
      "/api/export/compliance-evidence",
      { kind: "alert", id: "alt-1" },
      "alert-alt-1.pdf",
    );
  });

  it("keeps failed rows selected and warns when a bulk run partially fails", async () => {
    twoAlerts();
    h.transition.mutateAsync = vi
      .fn()
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("409 conflict"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    await selectAll();
    await userEvent.click(
      screen.getByRole("button", { name: "Close as Confirmed Leak" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(h.toast.warning).toHaveBeenCalledWith(
        "Close as confirmed leak: 1 succeeded, 1 failed. Failed rows stay selected.",
      ),
    );
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("reports total failure with an error toast", async () => {
    h.alerts = { data: [makeAlert({})], isLoading: false };
    h.transition.mutateAsync = vi.fn().mockRejectedValue(new Error("boom"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderPage();
    await selectAll();
    await userEvent.click(
      screen.getByRole("button", { name: "Close as False Positive" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(h.toast.error).toHaveBeenCalledWith(
        "Close as false positive failed for all 1 alerts. Check console.",
      ),
    );
  });
});

describe("ThreatsAlertsPage — deep link", () => {
  it("opens the sheet for a routed alert id", async () => {
    h.deepLink = { data: makeAlert({ alert_id: "a-9" }), error: null };
    renderPage("/alerts/a-9");
    await waitFor(() => expect(screen.getByText("What Happened")).toBeInTheDocument());
  });

  it("toasts when the routed alert does not exist", async () => {
    h.deepLink = { data: undefined, error: new Error("404") };
    renderPage("/alerts/missing");
    await waitFor(() =>
      expect(h.toast.error).toHaveBeenCalledWith("Alert not found"),
    );
  });
});
