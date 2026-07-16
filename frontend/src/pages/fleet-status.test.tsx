import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { DlpAlert, FleetTrust, Stats, Verify } from "@/api/types";

const h = vi.hoisted(() => ({
  stats: { data: undefined as Stats | undefined, isLoading: false },
  verify: { data: undefined as Verify | undefined, isLoading: false },
  trust: { data: undefined as FleetTrust | undefined, isLoading: false },
  alerts: { data: [] as DlpAlert[], isLoading: false },
  features: { signingEnabled: true },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useStats: () => h.stats,
  useVerify: () => h.verify,
  useFleetTrust: () => h.trust,
  useDlpAlerts: () => h.alerts,
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("@/hooks/use-agent-label", () => ({
  useAgentLabel: () => ({ shortLabel: (s: string) => `short(${s})` }),
}));
// Charts are covered visually elsewhere; jsdom has no layout, so recharts
// containers render nothing useful. Noop them and keep ChartCard real.
vi.mock("recharts", () => {
  const Noop = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Noop,
    AreaChart: Noop,
    Area: Noop,
    XAxis: Noop,
    YAxis: Noop,
    CartesianGrid: Noop,
    Tooltip: Noop,
    ReferenceArea: Noop,
    ReferenceLine: Noop,
    BarChart: Noop,
    Bar: Noop,
    Cell: Noop,
  };
});

import FleetStatusPage from "./fleet-status";

const stats: Stats = {
  total: 500,
  first_entry: null,
  last_entry: null,
  unique_agents: 3,
  unique_sessions: 10,
  activity: { "2026-07-01": 10, "2026-07-02": 30 },
  agents: { "agent:a": 300, "agent:b": 200 },
  action_types: {},
  upstreams: {},
};

const trust: FleetTrust = {
  trust_score: 87,
  tier: "Monitored",
  tier_key: "monitored",
  active_agents: 3,
  dimensions: {
    security: 90,
    compliance: 85,
    integrity: 88,
    reliability: 82,
    economics: 80,
  },
  tier_counts: { autonomous: 1, monitored: 2, human_approval: 0, isolated: 0 },
  signing_enabled: true,
  agents: [],
};

const goodVerify = {
  valid: true,
  entry_count: 100,
  chain_breaks: 0,
  signature_failures: 0,
  errors: [],
} as unknown as Verify;

let alertSeq = 0;
function alert(overrides: Partial<DlpAlert>): DlpAlert {
  alertSeq += 1;
  return {
    id: `a${alertSeq}`,
    serial_id: alertSeq,
    status: "open",
    severity: "low",
    score: 0.2,
    scanner: "bert",
    created_dt: "2026-07-01T12:00:00Z",
    entry_id: `e${alertSeq}`,
    session_id: "sess-1",
    ...overrides,
  } as unknown as DlpAlert;
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              <FleetStatusPage />
              <LocationProbe />
            </>
          }
        />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  alertSeq = 0;
  h.stats = { data: stats, isLoading: false };
  h.verify = { data: goodVerify, isLoading: false };
  h.trust = { data: trust, isLoading: false };
  h.alerts = { data: [], isLoading: false };
  h.features = { signingEnabled: true };
});

describe("FleetStatusPage — status banner", () => {
  it("shows skeletons while any query loads", () => {
    h.trust = { data: undefined, isLoading: true };
    const { container } = renderPage();
    expect(screen.getByText("Workforce Status")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("is OPERATIONAL when nothing is wrong", () => {
    renderPage();
    expect(screen.getByText("OPERATIONAL")).toBeInTheDocument();
    expect(
      screen.getByText("All systems normal · 3 active agents · Last scan 2 minutes ago"),
    ).toBeInTheDocument();
  });

  it("escalates to WARNING on open medium/high alerts", () => {
    h.alerts = {
      data: [alert({ severity: "medium" }), alert({ severity: "high" })],
      isLoading: false,
    };
    renderPage();
    expect(screen.getByText("WARNING")).toBeInTheDocument();
    expect(
      screen.getByText("2 alert(s) require attention · Review recommended"),
    ).toBeInTheDocument();
  });

  it("escalates to BREACH on a critical alert", () => {
    h.alerts = { data: [alert({ severity: "critical" })], isLoading: false };
    renderPage();
    expect(screen.getByText("BREACH")).toBeInTheDocument();
    expect(
      screen.getByText("1 critical incident(s) in progress · Immediate action required"),
    ).toBeInTheDocument();
  });

  it("treats a broken chain as BREACH only when signing is enabled", () => {
    h.verify = { data: { ...goodVerify, valid: false } as Verify, isLoading: false };
    renderPage();
    expect(screen.getByText("BREACH")).toBeInTheDocument();
  });

  it("ignores verification and locks the integrity KPI in the starter edition", () => {
    h.features = { signingEnabled: false };
    h.verify = { data: { ...goodVerify, valid: false } as Verify, isLoading: false };
    renderPage();
    expect(screen.getByText("OPERATIONAL")).toBeInTheDocument();
    expect(screen.getByText("Enterprise only")).toBeInTheDocument();
    expect(screen.queryByText("BROKEN")).not.toBeInTheDocument();
  });

  it("ignores closed alerts for the posture", () => {
    h.alerts = {
      data: [alert({ severity: "critical", status: "closed" })],
      isLoading: false,
    };
    renderPage();
    expect(screen.getByText("OPERATIONAL")).toBeInTheDocument();
  });
});

describe("FleetStatusPage — hero, KPIs, charts", () => {
  it("renders the trust hero with score, tier, and caption", () => {
    renderPage();
    expect(screen.getByText("87")).toBeInTheDocument();
    expect(screen.getByText("Monitored")).toBeInTheDocument();
    expect(screen.getByText("across 3 active agents")).toBeInTheDocument();
  });

  it("renders the KPI values", () => {
    h.alerts = {
      data: [
        alert({ severity: "critical" }),
        alert({ severity: "low" }),
        alert({ severity: "low", status: "closed" }),
      ],
      isLoading: false,
    };
    renderPage();
    const kpi = (label: string) =>
      screen.getByText(label).parentElement as HTMLElement;
    expect(kpi("Active Agents")).toHaveTextContent("3");
    // Open Alerts excludes the closed one.
    expect(kpi("Open Alerts")).toHaveTextContent("2");
    expect(kpi("Blocked Chains (24h)")).toHaveTextContent("1");
    expect(screen.getByText("VERIFIED")).toBeInTheDocument();
  });

  it("navigates to the drill-down pages from the KPI buttons", async () => {
    renderPage();
    await userEvent.click(screen.getByText("Open Alerts"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/threats-alerts");
  });

  it("shows BROKEN integrity when verification fails", () => {
    h.verify = { data: { ...goodVerify, valid: false } as Verify, isLoading: false };
    renderPage();
    expect(screen.getByText("BROKEN")).toBeInTheDocument();
  });

  it("renders both chart cards", () => {
    renderPage();
    expect(screen.getByText("Agent Activity (last 14 days)")).toBeInTheDocument();
    expect(screen.getByText("Top 5 Active Agents")).toBeInTheDocument();
  });
});

describe("FleetStatusPage — recent activity feed", () => {
  it("shows the empty note when there are no alerts", () => {
    renderPage();
    expect(screen.getByText("No recent activity.")).toBeInTheDocument();
  });

  it("lists open alerts with severity, agent excerpt, and serial", () => {
    h.alerts = {
      data: [alert({ severity: "critical", serial_id: 9, entry_id: "e9" })],
      isLoading: false,
    };
    renderPage();
    // Severity appears in the chip row and the feed row.
    expect(screen.getAllByText("CRITICAL").length).toBeGreaterThan(1);
    expect(screen.getByText("short(e9)")).toBeInTheDocument();
    expect(screen.getByText("ALT-0009 →")).toBeInTheDocument();
  });

  it("navigates to Threats & Alerts when a feed row is clicked", async () => {
    h.alerts = { data: [alert({ severity: "high" })], isLoading: false };
    renderPage();
    await userEvent.click(screen.getByText("ALT-0001 →"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/threats-alerts");
  });

  it("filters the feed by severity chip and resets on second click", async () => {
    h.alerts = {
      data: [
        alert({ severity: "critical", entry_id: "crit-entry" }),
        alert({ severity: "low", entry_id: "low-entry" }),
      ],
      isLoading: false,
    };
    renderPage();
    // Chip counts: All 2, CRITICAL 1, LOW 1.
    expect(screen.getByRole("button", { name: "All 2" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "CRITICAL 1" }));
    expect(screen.getByText("short(crit-entry)")).toBeInTheDocument();
    expect(screen.queryByText("short(low-entry)")).not.toBeInTheDocument();
    // Clicking the active chip resets back to "all".
    await userEvent.click(screen.getByRole("button", { name: "CRITICAL 1" }));
    expect(screen.getByText("short(low-entry)")).toBeInTheDocument();
  });

  it("shows a severity-specific empty note when the filter matches nothing", async () => {
    h.alerts = { data: [alert({ severity: "low" })], isLoading: false };
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "HIGH 0" }));
    expect(screen.getByText("No HIGH alerts in the feed.")).toBeInTheDocument();
  });
});
