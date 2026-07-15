import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Configuration, DlpHealth, VerificationRun, Verify } from "@/api/types";

const h = vi.hoisted(() => ({
  verify: {
    data: undefined as Verify | undefined,
    isLoading: false,
    dataUpdatedAt: 1,
  },
  config: { data: undefined as Configuration | undefined, isLoading: false },
  runs: { data: undefined as VerificationRun[] | undefined },
  dlpHealth: { data: undefined as DlpHealth | undefined },
  features: { signingEnabled: true },
  downloadPdf: vi.fn(),
  downloadFile: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useVerify: () => h.verify,
  useConfiguration: () => h.config,
  useVerificationRuns: () => h.runs,
  useDlpHealth: () => h.dlpHealth,
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  downloadPdf: (...args: unknown[]) => h.downloadPdf(...args),
  downloadFile: (...args: unknown[]) => h.downloadFile(...args),
}));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));
vi.mock("sonner", () => ({ toast: h.toast }));

import CompliancePage from "./compliance";

const goodVerify: Verify = {
  valid: true,
  entry_count: 1234,
  chain_breaks: 0,
  signature_failures: 0,
  errors: [],
  fingerprint: "SHA256:abcdef123456",
};

const config: Configuration = {
  edition: "enterprise",
  signing_enabled: true,
  enforcement_enabled: true,
  signing_mode: "tpm",
  tpm_available: true,
  algorithm: "Ed25519",
  key_paths: {
    private_key: { path: "/keys/priv", exists: true },
    public_key: { path: "/keys/pub", exists: true },
    tpm_key: { path: "/keys/tpm", exists: false },
  },
  configured_upstreams: [],
  ledger_backend: "postgres",
  ledger_entry_count: 1234,
  service_version: "1.2.3",
};

const healthyDlp: DlpHealth = {
  ok: true,
  scanners: [
    { name: "bert", ok: true },
    { name: "regex", ok: true },
  ] as DlpHealth["scanners"],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/compliance"]}>
      <Routes>
        <Route path="/compliance" element={<CompliancePage />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.verify = { data: goodVerify, isLoading: false, dataUpdatedAt: 1 };
  h.config = { data: config, isLoading: false };
  h.runs = { data: [] };
  h.dlpHealth = { data: healthyDlp };
  h.features = { signingEnabled: true };
  h.downloadPdf = vi.fn().mockResolvedValue(undefined);
  h.downloadFile = vi.fn().mockResolvedValue(undefined);
  h.toast.success.mockReset();
  h.toast.error.mockReset();
});

describe("CompliancePage — status hero and KPIs", () => {
  it("shows skeletons while loading", () => {
    h.verify = { data: undefined, isLoading: true, dataUpdatedAt: 0 };
    const { container } = renderPage();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("renders COMPLIANT with verification details when the chain is valid", () => {
    renderPage();
    expect(screen.getByText("COMPLIANT")).toBeInTheDocument();
    expect(
      screen.getByText(/All 1,234 entries verified · 0 chain breaks/),
    ).toBeInTheDocument();
    expect(screen.getByText("VERIFIED")).toBeInTheDocument();
    expect(screen.getByText("TPM")).toBeInTheDocument();
    expect(screen.getByText("Ed25519")).toBeInTheDocument();
    expect(screen.getByText("postgres")).toBeInTheDocument();
    // Fingerprint block renders with its copy affordance.
    expect(screen.getByText("SHA256:abcdef123456")).toBeInTheDocument();
  });

  it("renders NON-COMPLIANT with the error list when the chain is broken", () => {
    h.verify = {
      dataUpdatedAt: 1,
      isLoading: false,
      data: {
        valid: false,
        entry_count: 10,
        chain_breaks: 2,
        signature_failures: 1,
        errors: ["hash mismatch at seq 5", "bad signature at seq 7"],
      },
    };
    renderPage();
    expect(screen.getByText("NON-COMPLIANT")).toBeInTheDocument();
    expect(screen.getByText(/Chain integrity compromised/)).toBeInTheDocument();
    expect(screen.getByText("BROKEN")).toBeInTheDocument();
    expect(screen.getByText("Integrity Errors (2)")).toBeInTheDocument();
    expect(screen.getByText("hash mismatch at seq 5")).toBeInTheDocument();
  });

  it("locks signing panels in the sandbox edition", () => {
    h.features = { signingEnabled: false };
    renderPage();
    // LockedMetric tiles replace the live KPIs.
    expect(screen.getAllByText("Enterprise only").length).toBeGreaterThan(1);
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
  });
});

describe("CompliancePage — exports", () => {
  it("exports the compliance report PDF", async () => {
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export Compliance Report" }),
    );
    await waitFor(() =>
      expect(h.downloadPdf).toHaveBeenCalledWith(
        "/api/export/compliance-report",
        {},
        "compliance-report.pdf",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Compliance report downloaded");
  });

  it("exports the ledger CSV for the selected window", async () => {
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "Ledger Export (CSV)" }),
    );
    await waitFor(() =>
      expect(h.downloadFile).toHaveBeenCalledWith(
        "/api/export/ledger-csv",
        { window: "30d" },
        "ledger-30d.csv",
        "text/csv",
      ),
    );
    expect(h.toast.success).toHaveBeenCalledWith("Ledger CSV downloaded");
  });

  it("exports chain signatures as JSON", async () => {
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "Chain Signatures (JSON)" }),
    );
    await waitFor(() =>
      expect(h.downloadFile).toHaveBeenCalledWith(
        "/api/export/chain-signatures",
        { window: "30d" },
        "chain-signatures-30d.json",
        "application/json",
      ),
    );
  });

  it("surfaces export failures as an error toast", async () => {
    h.downloadPdf = vi.fn().mockRejectedValue(new Error("503 export"));
    renderPage();
    await userEvent.click(
      screen.getByRole("button", { name: "🛡 Export Compliance Report" }),
    );
    await waitFor(() => expect(h.toast.error).toHaveBeenCalledWith("503 export"));
  });

  it("routes to the audit API docs", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Audit API →" }));
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
  });
});

describe("CompliancePage — verification history", () => {
  it("shows the empty note when no runs exist", () => {
    renderPage();
    expect(screen.getByText(/No verification runs yet/)).toBeInTheDocument();
  });

  it("lists pass and fail runs", () => {
    h.runs = {
      data: [
        {
          run_id: "r1",
          run_at: "2026-07-01T10:00:00Z",
          total_entries: 100,
          verified_entries: 100,
          chain_breaks: 0,
          signature_failures: 0,
          first_broken_seq: null,
          signature_alg: "Ed25519",
          status: "pass",
          error_sample: [],
        },
        {
          run_id: "r2",
          run_at: "2026-07-02T10:00:00Z",
          total_entries: 100,
          verified_entries: 90,
          chain_breaks: 1,
          signature_failures: 2,
          first_broken_seq: 91,
          signature_alg: "Ed25519",
          status: "fail",
          error_sample: [],
        },
      ],
    };
    renderPage();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("1 chain breaks")).toBeInTheDocument();
    expect(screen.getByText("2 sig fails")).toBeInTheDocument();
  });
});

describe("CompliancePage — evidence coverage", () => {
  it("marks every framework COVERED when all signals are live", () => {
    renderPage();
    expect(screen.getAllByText("COVERED")).toHaveLength(4);
    expect(screen.queryByText("PARTIAL")).not.toBeInTheDocument();
  });

  it("degrades to PARTIAL with reasons when scanners are down", () => {
    h.dlpHealth = {
      data: {
        ok: false,
        scanners: [
          { name: "bert", ok: false },
          { name: "regex", ok: false },
        ] as DlpHealth["scanners"],
      },
    };
    renderPage();
    expect(screen.getAllByText("PARTIAL").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/DLP scanners unreachable/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Regex DLP scanner unreachable/)).toBeInTheDocument();
  });

  it("flags NIS-2 when signing is not configured", () => {
    h.config = {
      isLoading: false,
      data: { ...config, signing_enabled: false },
    };
    renderPage();
    expect(screen.getByText(/— Signing not configured/)).toBeInTheDocument();
  });
});
