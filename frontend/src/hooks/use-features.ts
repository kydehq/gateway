import { useConfiguration } from "@/api/queries";

// Edition / feature gate. Mirrors use-me.ts: wraps the configuration query
// and exposes convenience booleans for the enterprise features (independent audit
// signing, inline enforcement) that the sandbox image physically lacks.
//
// While the config is still loading we default the feature flags to ENABLED.
// The common case is the enterprise edition, so this avoids flashing a locked /
// "upgrade" state on enterprise installs; sandbox resolves to locked once the
// config lands. Gate *enterprise* controls on `signingEnabled` / `enforcementEnabled`
// and show the upgrade hint when they are false.
export function useFeatures() {
  const q = useConfiguration();
  const cfg = q.data;
  const loaded = !!cfg;
  return {
    ...q,
    edition: cfg?.edition ?? "enterprise",
    isSandbox: loaded ? cfg!.edition === "sandbox" : false,
    // Default true until known (see note above).
    signingEnabled: loaded ? !!cfg!.signing_enabled : true,
    enforcementEnabled: loaded ? !!cfg!.enforcement_enabled : true,
  };
}
