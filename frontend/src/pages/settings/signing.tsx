import { Cpu, KeyRound } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfiguration } from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";
import { UpgradeNotice } from "@/components/shared/upgrade-lock";
import { KeyValueRow } from "./_shared";

export default function SettingsSigningPage() {
  const { data: config, isLoading } = useConfiguration();
  const { signingEnabled } = useFeatures();

  return (
    <>
      <h2 className="mb-3 text-sm font-semibold tracking-tight">
        Signing &amp; cryptography
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Ledger entries are signed; the public key fingerprint below is what
        verifiers compare against.
      </p>
      {isLoading || !config ? (
        <Skeleton className="h-48" />
      ) : !signingEnabled ? (
        <UpgradeNotice title="Independent audit signing">
          The sandbox edition keeps the ledger hash-chained and
          tamper-evident, but cryptographic signing keys (Ed25519 / TPM) are a
          enterprise feature. Upgrade to KYDE Enterprise for independently verifiable,
          signed audit records.
        </UpgradeNotice>
      ) : (
        <Card>
          <CardContent className="p-5">
            <KeyValueRow
              label="Key location"
              value={
                <span className="inline-flex items-center gap-1.5">
                  {config.tpm_available ? (
                    <KeyRound className="h-3 w-3 text-success" />
                  ) : (
                    <Cpu className="h-3 w-3 text-muted-foreground" />
                  )}
                  {config.signing_mode}
                </span>
              }
            />
            <KeyValueRow label="Key type" value={config.algorithm ?? "—"} />
            <KeyValueRow
              label="Fingerprint"
              value={config.public_key_fingerprint ?? "—"}
              copyable={config.public_key_fingerprint ?? undefined}
            />
            <KeyValueRow
              label="Private key"
              value={
                <span
                  className={
                    config.key_paths?.private_key.exists
                      ? ""
                      : "text-destructive"
                  }
                >
                  {config.key_paths?.private_key.path}
                  {!config.key_paths?.private_key.exists ? " (missing)" : ""}
                </span>
              }
            />
            <KeyValueRow
              label="Public key"
              value={
                <span
                  className={
                    config.key_paths?.public_key.exists ? "" : "text-destructive"
                  }
                >
                  {config.key_paths?.public_key.path}
                  {!config.key_paths?.public_key.exists ? " (missing)" : ""}
                </span>
              }
            />
            <KeyValueRow
              label="TPM key"
              value={
                config.key_paths?.tpm_key.exists ? (
                  config.key_paths?.tpm_key.path
                ) : (
                  <span className="text-muted-foreground">
                    — (not provisioned)
                  </span>
                )
              }
            />
          </CardContent>
        </Card>
      )}
    </>
  );
}
