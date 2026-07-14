import { PageHeader } from "@/components/shared/page-header";
import { ReadOnlyBadge } from "@/components/shared/read-only-badge";
import { AgentNamesSection } from "@/components/shared/agent-names-section";
import { HostNamesSection } from "@/components/shared/host-names-section";
import { useMe } from "@/hooks/use-me";

// Operator-facing display labels: the human-readable names that surface across
// the UI. Agent display names and per-IP host name overrides. (The agent
// *routing* endpoints moved to the LLM Routing page, where they sit next to the
// upstream they forward to.)
export default function LabelsPage() {
  const { isAdmin } = useMe();
  return (
    <>
      <PageHeader
        title="Labels"
        description="Agent display names and host name overrides. These names surface across Sessions, Audit Log, Network Map, and Threats detail."
        actions={isAdmin ? undefined : <ReadOnlyBadge />}
      />

      <section className="mb-8">
        <h2 className="mb-1 text-sm font-semibold tracking-tight">
          Agent names
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Human-readable display name per agent. Empty falls back to a
          hash-derived label.
        </p>
        <AgentNamesSection readOnly={!isAdmin} />
      </section>

      <section className="mb-8">
        <h2 className="mb-1 text-sm font-semibold tracking-tight">
          Host names
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Hostnames per observed IP. Admin labels override reverse-DNS. Names
          render as <code className="font-mono text-[11px]">hostname (ip)</code>{" "}
          across Network Map, Agent detail, Audit Log previews, and Threats
          detail.
        </p>
        <HostNamesSection readOnly={!isAdmin} />
      </section>
    </>
  );
}
