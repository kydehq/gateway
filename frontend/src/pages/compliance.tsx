import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle, Circle, Copy, XCircle } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  STATS_WINDOWS,
  useConfiguration,
  useDlpHealth,
  useVerificationRuns,
  useVerify,
  type StatsWindow,
} from "@/api/queries";
import type { Configuration, DlpHealth, Verify } from "@/api/types";
import { downloadFile, downloadPdf } from "@/api/client";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useFeatures } from "@/hooks/use-features";
import { EnterpriseLock, LockedMetric, LockedPanel } from "@/components/shared/upgrade-lock";

const WINDOW_LABEL: Record<StatsWindow, string> = {
  "1h": "Last 1h",
  "24h": "Last 24h",
  "7d": "Last 7d",
  "30d": "Last 30d",
  "90d": "Last 90d",
  all: "All time",
};

async function exportComplianceReport() {
  try {
    await downloadPdf("/api/export/compliance-report", {}, "compliance-report.pdf");
    toast.success("Compliance report downloaded");
  } catch (err) {
    toast.error((err as Error).message || "Export failed");
  }
}

async function exportLedgerCsv(window: StatsWindow) {
  try {
    await downloadFile(
      "/api/export/ledger-csv",
      { window },
      `ledger-${window}.csv`,
      "text/csv",
    );
    toast.success("Ledger CSV downloaded");
  } catch (err) {
    toast.error((err as Error).message || "Export failed");
  }
}

async function exportChainSignatures(window: StatsWindow) {
  try {
    await downloadFile(
      "/api/export/chain-signatures",
      { window },
      `chain-signatures-${window}.json`,
      "application/json",
    );
    toast.success("Chain signatures downloaded");
  } catch (err) {
    toast.error((err as Error).message || "Export failed");
  }
}

// Evidence the gateway can demonstrate for each regulatory article.
// `present` is derived from real signals so the badge reflects current
// state of the system, not a hard-coded editorial claim. Each predicate
// states *what the gateway can show an auditor* — not whether the org
// is compliant overall (RoPA documents, lawful basis, controller
// agreements etc. live outside this system and are not evidenced here).
interface ArticleEvidence {
  label: string;
  present: boolean;
  reason?: string;
}

interface FrameworkEvidence {
  framework: string;
  articles: ArticleEvidence[];
  status: "covered" | "partial";
}

function deriveEvidence(
  verify: Verify | undefined,
  dlpHealth: DlpHealth | undefined,
  config: Configuration | undefined,
): FrameworkEvidence[] {
  const ledgerOk = verify?.valid === true && (verify?.entry_count ?? 0) > 0;
  const ledgerHasEntries = (verify?.entry_count ?? 0) > 0;
  // BERT + regex scanners ship with rules preloaded and run on every
  // request — they cannot be disabled. The only failure mode is the
  // sidecar being unreachable, so "DLP active" === sidecar healthy.
  // Data minimization specifically needs the regex scanner (entity-level
  // matching), so we surface it separately.
  const bertHealthy = dlpHealth?.scanners.find((s) => s.name === "bert")?.ok ?? false;
  const regexHealthy = dlpHealth?.scanners.find((s) => s.name === "regex")?.ok ?? false;
  const dlpActive = bertHealthy || regexHealthy;
  const dlpMinimizationActive = regexHealthy;
  // Starter edition reports signing_mode "disabled" (a truthy string), so
  // gate on the explicit flag — otherwise NIS-2 Art. 21 would falsely read as
  // signing-backed in the free, unsigned edition.
  const signingConfigured = config?.signing_enabled === true;

  const frameworks: FrameworkEvidence[] = [
    {
      framework: "EU AI Act",
      articles: [
        {
          label: "Art. 9 — Risk management",
          present: dlpActive,
          reason: dlpActive ? undefined : "DLP scanners unreachable",
        },
        {
          label: "Art. 12 — Record keeping",
          present: ledgerOk,
          reason: ledgerOk ? undefined : "Ledger empty or chain not verified",
        },
        {
          label: "Art. 13 — Transparency",
          present: ledgerHasEntries,
          reason: ledgerHasEntries ? undefined : "No ledger entries yet",
        },
      ],
      status: "covered",
    },
    {
      framework: "DORA (EU 2022/2554)",
      articles: [
        {
          label: "Art. 8 — ICT risk management",
          present: dlpActive && ledgerOk,
          reason: !dlpActive
            ? "DLP scanners unreachable"
            : !ledgerOk
              ? "Ledger not verified"
              : undefined,
        },
        {
          label: "Art. 10 — Detection",
          present: dlpActive,
          reason: dlpActive ? undefined : "DLP scanners unreachable",
        },
      ],
      status: "covered",
    },
    {
      framework: "NIS-2 Directive",
      articles: [
        {
          label: "Art. 21 — Security measures",
          present: dlpActive && signingConfigured,
          reason: !signingConfigured
            ? "Signing not configured"
            : !dlpActive
              ? "DLP scanners unreachable"
              : undefined,
        },
        {
          label: "Art. 23 — Incident reporting",
          present: ledgerHasEntries,
          reason: ledgerHasEntries ? undefined : "No incidents recorded yet",
        },
      ],
      status: "covered",
    },
    {
      framework: "GDPR Art. 30",
      articles: [
        {
          label: "Records of processing",
          present: ledgerOk,
          reason: ledgerOk ? undefined : "Ledger empty or chain not verified",
        },
        {
          label: "Data minimization evidence",
          present: dlpMinimizationActive,
          reason: dlpMinimizationActive ? undefined : "Regex DLP scanner unreachable",
        },
      ],
      status: "covered",
    },
  ];

  for (const f of frameworks) {
    f.status = f.articles.every((a) => a.present) ? "covered" : "partial";
  }
  return frameworks;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Copy failed");
    }
  };

  return (
    <button
      onClick={handleCopy}
      className="shrink-0 ml-2 rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
      title="Copy to clipboard"
    >
      {copied ? <CheckCircle className="h-3.5 w-3.5 text-brand-green" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

export default function CompliancePage() {
  const navigate = useNavigate();
  const { data: verify, isLoading: verifyLoading, dataUpdatedAt } = useVerify();
  const [exportWindow, setExportWindow] = useState<StatsWindow>("30d");
  const { data: config, isLoading: configLoading } = useConfiguration();
  const { data: runs } = useVerificationRuns(10);
  const { data: dlpHealth } = useDlpHealth();

  const { signingEnabled } = useFeatures();
  const isLoading = verifyLoading || configLoading;
  const evidence = deriveEvidence(verify, dlpHealth, config);

  if (isLoading) {
    return (
      <>
        <PageHeader title="Compliance" description="Ledger integrity and regulatory evidence status." />
        <Skeleton className="h-28 w-full mb-7" />
        <div className="grid grid-cols-3 gap-3 mb-7">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
        <Skeleton className="h-40 rounded-lg" />
      </>
    );
  }

  const isValid = verify?.valid ?? false;
  const statusStyle = isValid
    ? "bg-success/10 border border-success/20 text-success"
    : "bg-sev-critical/10 border border-sev-critical/20 text-sev-critical";

  const contextLine = isValid
    ? `All ${verify?.entry_count?.toLocaleString() ?? "—"} entries verified · ${verify?.chain_breaks ?? 0} chain breaks · ${verify?.signature_failures ?? 0} signature failures`
    : `Chain integrity compromised · ${verify?.chain_breaks ?? "?"} break(s) · ${verify?.signature_failures ?? "?"} signature failure(s)`;

  return (
    <>
      <PageHeader
        title="Compliance"
        description="Cryptographic ledger integrity, public key, and regulatory evidence status."
        lastUpdated={dataUpdatedAt}
        actions={
          <Button variant="outline" size="sm" onClick={exportComplianceReport}>
            🛡 Export Compliance Report
          </Button>
        }
      />

      {/* Hero status block */}
      <div className={cn("rounded-lg p-6 mb-7", statusStyle)}>
        <div className="flex items-center gap-3 mb-1">
          {isValid
            ? <CheckCircle className="h-6 w-6 text-brand-green" />
            : <XCircle className="h-6 w-6 text-sev-critical" />}
          <div className="text-2xl font-bold tracking-tight">
            {isValid ? "COMPLIANT" : "NON-COMPLIANT"}
          </div>
        </div>
        <div className="text-sm">{contextLine}</div>
      </div>

      {/* KPI block */}
      <div className="grid grid-cols-3 gap-3 mb-7">
        <MetricCard
          label="Ledger Entries"
          value={verify?.entry_count?.toLocaleString() ?? "—"}
        />
        {signingEnabled ? (
          <>
            <MetricCard
              label="Chain Integrity"
              value={isValid ? "VERIFIED" : "BROKEN"}
              tone={isValid ? "success" : "destructive"}
            />
            <MetricCard
              label="Signing Mode"
              value={config?.signing_mode?.toUpperCase() ?? "—"}
              tone={config?.signing_mode === "tpm" ? "success" : undefined}
            />
          </>
        ) : (
          <>
            <LockedMetric label="Chain Integrity" />
            <LockedMetric label="Signing Mode" />
          </>
        )}
      </div>

      {/* Detail cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 mb-7">
        {signingEnabled ? (
        <div className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Verification Details</p>
          <div className="space-y-2 text-sm">
            {[
              ["Entries", verify?.entry_count?.toLocaleString() ?? "—"],
              ["Chain Breaks", String(verify?.chain_breaks ?? "—")],
              ["Sig Failures", String(verify?.signature_failures ?? "—")],
              ["Algorithm", config?.algorithm ?? "—"],
              ["Backend", config?.ledger_backend ?? "—"],
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between">
                <span className="text-muted-foreground">{label}</span>
                <span className="font-mono text-xs">{val}</span>
              </div>
            ))}
          </div>
        </div>
        ) : (
          <LockedPanel title="Verification Details" />
        )}

        {signingEnabled ? (
        <div className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Signing Status</p>
          <div className="space-y-2 text-sm">
            {[
              ["Mode", config?.signing_mode ?? "—"],
              ["TPM Available", config?.tpm_available ? "Yes" : "No"],
              ["Private Key", config?.key_paths?.private_key?.exists ? "Present" : "Missing"],
              ["Public Key", config?.key_paths?.public_key?.exists ? "Present" : "Missing"],
              ["Service Ver.", config?.service_version ?? "—"],
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between">
                <span className="text-muted-foreground">{label}</span>
                <span className="font-mono text-xs">{val}</span>
              </div>
            ))}
          </div>
        </div>
        ) : (
          <LockedPanel title="Signing Status" />
        )}

        <div className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">Evidence Export</p>
          <div className="mb-3">
            <label className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1">
              Time window
            </label>
            <Select value={exportWindow} onValueChange={(v) => setExportWindow(v as StatsWindow)}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATS_WINDOWS.map((w) => (
                  <SelectItem key={w} value={w}>{WINDOW_LABEL[w]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1 text-[10px] text-muted-foreground">
              Applies to CSV and JSON exports. The PDF report covers the full chain.
            </p>
          </div>
          <div className="space-y-2">
            <Button variant="outline" size="sm" className="w-full justify-start" onClick={exportComplianceReport}>
              🛡 Full Compliance Report (PDF)
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => exportLedgerCsv(exportWindow)}>
              Ledger Export (CSV)
            </Button>
            <EnterpriseLock
              locked={!signingEnabled}
              hint="Chain signatures require independent audit signing — available in the KYDE Enterprise edition."
              className="block w-full"
            >
              <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => exportChainSignatures(exportWindow)}>
                Chain Signatures (JSON)
              </Button>
            </EnterpriseLock>
            <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={() => navigate("/compliance/api-docs")}>
              Audit API →
            </Button>
          </div>
        </div>
      </div>

      {/* Public key fingerprint */}
      {verify?.fingerprint && (
        <section className="mb-7">
          <h2 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Public Key Fingerprint
          </h2>
          <div className="flex items-start rounded-md border bg-muted/40 px-4 py-3">
            <pre className="font-mono text-xs text-foreground break-all whitespace-pre-wrap flex-1">
              {verify.fingerprint}
            </pre>
            <CopyButton text={verify.fingerprint} />
          </div>
        </section>
      )}

      {/* Verification errors */}
      {verify?.errors && verify.errors.length > 0 && (
        <section className="mb-7">
          <h2 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Integrity Errors ({verify.errors.length})
          </h2>
          <ul className="space-y-2">
            {verify.errors.map((err, i) => (
              <li
                key={i}
                className="rounded-md border border-destructive/50 bg-destructive/5 px-4 py-3 font-mono text-sm text-destructive"
              >
                {err}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Verification history — real data from /api/verification-runs.
          Every /api/verify call appends one row, so empty just means the
          chain hasn't been verified yet. */}
      <section className="mb-7">
        <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Verification History
        </h2>
        <div className="rounded-md border divide-y">
          {(!runs || runs.length === 0) ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No verification runs yet. Run a chain verification to populate this log.
            </div>
          ) : (
            runs.map((run) => {
              const pass = run.status === "pass";
              return (
                <div key={run.run_id} className="flex items-center justify-between px-4 py-3 text-sm">
                  <div className="flex items-center gap-3">
                    {pass
                      ? <CheckCircle className="h-4 w-4 text-brand-green shrink-0" />
                      : <XCircle className="h-4 w-4 text-sev-critical shrink-0" />}
                    <span className="font-mono text-xs">{run.run_at.slice(0, 19).replace("T", " ")}</span>
                  </div>
                  <div className="flex items-center gap-6 text-xs text-muted-foreground">
                    <span>{run.total_entries.toLocaleString()} entries</span>
                    <span>{run.chain_breaks} chain breaks</span>
                    <span>{run.signature_failures} sig fails</span>
                    <span className={cn("font-semibold", pass ? "text-brand-green" : "text-sev-critical")}>
                      {pass ? "PASS" : "FAIL"}
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </section>

      {/* Evidence coverage — derived from live signals */}
      <section>
        <h2 className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
          Evidence Coverage
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Per-article signals the gateway can demonstrate. COVERED means every listed
          evidence item has a live signal; PARTIAL means at least one is missing.
          Organizational documentation (RoPA, lawful basis, policies) lives outside
          this system and is not evidenced here.
        </p>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {evidence.map((reg) => (
            <div key={reg.framework} className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold">{reg.framework}</span>
                <span
                  className={cn(
                    "text-xs font-semibold px-2 py-0.5 rounded border",
                    reg.status === "covered"
                      ? "bg-success/10 text-success border-success/20"
                      : "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
                  )}
                >
                  {reg.status === "covered" ? "COVERED" : "PARTIAL"}
                </span>
              </div>
              <ul className="space-y-1">
                {reg.articles.map((a) => (
                  <li
                    key={a.label}
                    className="text-xs text-muted-foreground flex items-start gap-1.5"
                    title={a.reason}
                  >
                    {a.present ? (
                      <CheckCircle className="h-3 w-3 text-brand-green mt-0.5 shrink-0" />
                    ) : (
                      <Circle className="h-3 w-3 text-sev-medium mt-0.5 shrink-0" />
                    )}
                    <span className={a.present ? undefined : "text-sev-medium"}>
                      {a.label}
                      {!a.present && a.reason ? (
                        <span className="ml-1 text-muted-foreground">— {a.reason}</span>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
