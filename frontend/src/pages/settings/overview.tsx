import { FileText } from "lucide-react";
import { MetricCard } from "@/components/shared/metric-card";
import { RelativeTime } from "@/components/shared/relative-time";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useConfiguration, useServiceMetrics } from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";
import { fmtTokens } from "@/lib/format";
import { cn } from "@/lib/utils";
import { KeyValueRow, fmtBytes, fmtUptime } from "./_shared";

// Settings landing page — service health at a glance plus the
// "config.yaml is the source of truth" reminder that used to sit at
// the top of the old monolithic Settings page.
export default function SettingsOverviewPage() {
  const { data: config, isLoading: cfgLoading } = useConfiguration();
  const { data: metrics, isLoading: mLoading } = useServiceMetrics();
  const { signingEnabled } = useFeatures();

  return (
    <>
      <Alert className="mb-7 border-info/40 bg-info/5 text-foreground [&>svg]:text-info">
        <FileText className="h-4 w-4" />
        <AlertDescription className="text-sm">
          Most configuration (upstreams, DLP scanners, signing keys) is
          file-based and read-only from this UI. Edits happen in{" "}
          <code className="font-mono text-xs">config.yaml</code> and{" "}
          <code className="font-mono text-xs">dlp-patterns/*.yaml</code>, then
          require a service restart.
        </AlertDescription>
      </Alert>

      <h2 className="mb-3 text-sm font-semibold tracking-tight">System</h2>
      {mLoading || !metrics || cfgLoading || !config ? (
        <div className="mb-7 grid grid-cols-2 gap-3 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : (
        <div className="mb-7 grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard label="Service Version" small value={config.service_version ?? "—"} />
          <MetricCard label="Uptime" small value={fmtUptime(metrics.uptime_seconds)} />
          <MetricCard
            label="Started"
            small
            value={<RelativeTime value={metrics.service_start_time} />}
          />
          <MetricCard label="Ledger Size" small value={fmtBytes(metrics.ledger_size_bytes)} />
        </div>
      )}

      <h2 className="mb-3 text-sm font-semibold tracking-tight">
        Operational metrics
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Throughput and chain health, refreshed every 30 seconds.
      </p>
      {mLoading || !metrics ? (
        <Skeleton className="h-24" />
      ) : (
        <Card>
          <CardContent className="p-5">
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-3">
              <KeyValueRow
                label="Total entries"
                value={metrics.total_entries.toLocaleString()}
              />
              <KeyValueRow
                label="Entries / hr (24h)"
                value={fmtTokens(metrics.entries_per_hour_24h)}
              />
              <KeyValueRow
                label="Entries / hr (1h)"
                value={fmtTokens(metrics.entries_per_hour_1h)}
              />
              <KeyValueRow
                label="Signature success"
                value={
                  signingEnabled ? (
                    (metrics.signature_success_rate * 100).toFixed(2) + "%"
                  ) : (
                    <span
                      className="text-muted-foreground"
                      title="Entries are hash-chained but not cryptographically signed. Independent audit signing is available in the KYDE Enterprise edition."
                    >
                      Unsigned
                    </span>
                  )
                }
              />
              <KeyValueRow
                label="Tool-call ratio"
                value={(metrics.tool_call_ratio * 100).toFixed(1) + "%"}
              />
              <KeyValueRow
                label="Chain integrity"
                value={
                  signingEnabled ? (
                    <span
                      className={cn(
                        "font-semibold",
                        metrics.chain_integrity.valid
                          ? "text-success"
                          : "text-destructive",
                      )}
                    >
                      {metrics.chain_integrity.valid
                        ? "VERIFIED"
                        : `BROKEN (${metrics.chain_integrity.break_count})`}
                    </span>
                  ) : (
                    <span
                      className="text-muted-foreground"
                      title="Cryptographic integrity verification is available in the KYDE Enterprise edition."
                    >
                      Enterprise
                    </span>
                  )
                }
              />
            </div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
