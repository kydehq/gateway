import { PageHeader } from "@/components/shared/page-header";
import { MetricCard } from "@/components/shared/metric-card";
import { Skeleton } from "@/components/ui/skeleton";
import { useVerify } from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";
import { UpgradeNotice } from "@/components/shared/upgrade-lock";

export default function IntegrityPage() {
  const { data: v, isLoading, isError, error, dataUpdatedAt } = useVerify();
  const { signingEnabled } = useFeatures();

  // The verifiable audit ledger (cryptographic integrity verification +
  // signatures) is an Enterprise feature. The sandbox edition is
  // observe-only, so this page is locked behind an upgrade notice.
  if (!signingEnabled) {
    return (
      <>
        <PageHeader
          title="Data Integrity Verification"
          description="Cryptographic verification of the audit ledger"
        />
        <UpgradeNotice title="Verifiable audit ledger">
          Cryptographic integrity verification and signed audit records are part
          of the KYDE Enterprise edition. The sandbox edition runs in
          observe-only mode — detection and alerts work, but the tamper-proof,
          independently verifiable audit ledger requires an upgrade.
        </UpgradeNotice>
      </>
    );
  }

  if (isLoading) {
    return (
      <>
        <PageHeader title="Data Integrity Verification" description="Cryptographic verification of the entire ledger" />
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      </>
    );
  }

  if (isError || !v) {
    return (
      <>
        <PageHeader title="Data Integrity Verification" />
        <p className="text-sm text-destructive">{(error as Error)?.message ?? "Failed to load verification."}</p>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Data Integrity Verification"
        description="Cryptographic verification of data integrity across the entire ledger"
        lastUpdated={dataUpdatedAt}
      />

      <div className="mb-7 grid grid-cols-1 gap-3 md:grid-cols-3">
        <MetricCard label="Entries Verified" value={v.entry_count} />
        <MetricCard
          label="Integrity Breaks"
          value={v.chain_breaks}
          tone={v.chain_breaks ? "destructive" : "default"}
        />
        <MetricCard
          label="Signature Failures"
          value={v.signature_failures}
          tone={v.signature_failures ? "destructive" : "default"}
        />
      </div>

      {v.fingerprint ? (
        <section className="mb-7">
          <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Public Key Fingerprint
          </h2>
          <pre className="rounded-md border border-border bg-muted/40 px-4 py-3 font-mono text-xs text-foreground break-all whitespace-pre-wrap">
            {v.fingerprint}
          </pre>
        </section>
      ) : null}

      {v.errors && v.errors.length > 0 ? (
        <section>
          <h2 className="mb-3 font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-foreground">
            Errors ({v.errors.length})
          </h2>
          <ul className="space-y-2">
            {v.errors.map((err, i) => (
              <li
                key={i}
                className="rounded-md border border-destructive/50 bg-destructive/5 px-4 py-3 font-mono text-sm text-destructive"
              >
                {err}
              </li>
            ))}
          </ul>
        </section>
      ) : v.valid ? (
        <p className="text-sm text-success">All checks passed.</p>
      ) : null}
    </>
  );
}
