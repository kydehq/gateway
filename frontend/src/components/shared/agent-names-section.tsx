import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Save, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { RelativeTime } from "./relative-time";
import { useAgents, useUpdateAgent } from "@/api/queries";
import type { Agent } from "@/api/types";
import { getAgentDisplayName } from "@/lib/agent-names";

// Admin-only section for naming agents. Sets the `display_name` column on
// the `agents` table; the change propagates everywhere the frontend renders
// an agent label via getAgentDisplayName().
export function AgentNamesSection({ readOnly = false }: { readOnly?: boolean }) {
  const { data: agents, isLoading } = useAgents();

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }

  if (!agents || agents.length === 0) {
    return <p className="text-sm text-muted-foreground">No agents observed yet.</p>;
  }

  return (
    <div className="space-y-2">
      {agents.map((a) => (
        <AgentRow key={a.agent_id} agent={a} readOnly={readOnly} />
      ))}
    </div>
  );
}

function AgentRow({ agent, readOnly }: { agent: Agent; readOnly: boolean }) {
  const [draft, setDraft] = useState(agent.display_name ?? "");
  useEffect(() => {
    setDraft(agent.display_name ?? "");
  }, [agent.display_name]);

  const update = useUpdateAgent();
  const trimmed = draft.trim();
  const currentName = agent.display_name ?? "";
  const dirty = trimmed !== currentName;
  const fallback = getAgentDisplayName(agent.agent_id);

  const onSave = async () => {
    try {
      await update.mutateAsync({
        agent_id: agent.agent_id,
        display_name: trimmed === "" ? null : trimmed,
      });
      toast.success("Agent name saved");
    } catch (err) {
      toast.error((err as Error).message || "Save failed");
    }
  };

  const onClear = async () => {
    try {
      await update.mutateAsync({ agent_id: agent.agent_id, display_name: null });
      toast.success("Agent name cleared");
    } catch (err) {
      toast.error((err as Error).message || "Clear failed");
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">{agent.agent_id}</span>
        <span className="text-[11px] text-muted-foreground">
          {agent.entry_count} entries · {agent.session_count} sessions · last seen{" "}
          <RelativeTime value={agent.last_seen_dt} />
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={draft}
          placeholder={fallback}
          onChange={(e) => setDraft(e.target.value)}
          disabled={readOnly}
          className="max-w-md font-mono text-sm"
        />
        <Button size="sm" onClick={onSave} disabled={readOnly || !dirty || update.isPending}>
          <Save className="mr-1 h-3 w-3" />
          Save
        </Button>
        {agent.display_name ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onClear}
            disabled={readOnly || update.isPending}
          >
            <X className="mr-1 h-3 w-3" />
            Clear
          </Button>
        ) : null}
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Empty = use the hash-derived default ({fallback}).
      </p>
    </div>
  );
}
