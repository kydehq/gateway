import { useMemo } from "react";
import { useAgents } from "@/api/queries";
import { getAgentDisplayName, getAgentShortName } from "@/lib/agent-names";

// Returns a pair of label functions that honor admin-supplied `display_name`
// from the agents table, falling back to the hash-derived label when none
// is set. TanStack Query dedupes the underlying /api/agents fetch so calling
// this from every page that renders agent names is cheap.
//
// Usage:
//   const { label, shortLabel } = useAgentLabel();
//   <span>{shortLabel(row.agent_id)}</span>
export function useAgentLabel() {
  const { data: agents } = useAgents();

  return useMemo(() => {
    const byId = new Map<string, string>();
    for (const a of agents ?? []) {
      if (a.display_name) byId.set(a.agent_id, a.display_name);
    }

    const lookup = (id: string) =>
      byId.has(id) ? { id, display_name: byId.get(id)! } : id;

    return {
      label: (id: string) => getAgentDisplayName(lookup(id)),
      shortLabel: (id: string) => getAgentShortName(lookup(id)),
    };
  }, [agents]);
}
