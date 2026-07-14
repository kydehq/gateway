import { SettingField } from "@/components/shared/setting-field";
import { Skeleton } from "@/components/ui/skeleton";
import { useSettings } from "@/api/queries";

export default function SettingsRuntimePage() {
  const { data: runtimeSettings, isLoading } = useSettings();

  return (
    <>
      <h2 className="mb-3 text-sm font-semibold tracking-tight">
        Runtime tuning
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Whitelisted values that can be changed at runtime without redeploying.
        Changes apply within ~5 seconds across all workers; every change is
        recorded in the signed audit ledger.
      </p>
      {isLoading || !runtimeSettings ? (
        <div className="space-y-2">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
      ) : runtimeSettings.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No runtime-tunable settings.
        </p>
      ) : (
        <div className="space-y-3">
          {/* SMTP_* live on their own page now — filter them out so they
              don't double-render here. */}
          {runtimeSettings
            .filter((s) => !s.key.startsWith("SMTP_"))
            .map((s) => (
              <SettingField key={s.key} entry={s} />
            ))}
        </div>
      )}
    </>
  );
}
