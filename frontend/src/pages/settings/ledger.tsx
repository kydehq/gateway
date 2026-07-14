import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfiguration } from "@/api/queries";
import { KeyValueRow } from "./_shared";

export default function SettingsLedgerPage() {
  const { data: config, isLoading } = useConfiguration();

  return (
    <>
      <h2 className="mb-3 text-sm font-semibold tracking-tight">Ledger</h2>
      {isLoading || !config ? (
        <Skeleton className="h-20" />
      ) : (
        <Card>
          <CardContent className="p-5">
            <KeyValueRow label="Backend" value={config.ledger_backend} />
            <KeyValueRow
              label="Entry count"
              value={config.ledger_entry_count.toLocaleString()}
            />
          </CardContent>
        </Card>
      )}
    </>
  );
}
