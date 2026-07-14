import { Network } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { AgentEndpoints } from "@/components/shared/agent-endpoints";
import { Alert, AlertDescription } from "@/components/ui/alert";

// The LLM routing table, in one place: for each configured provider it shows
// the full path a request takes — the gateway URL an agent points its SDK at
// (ingress), the matching env var, and the upstream Kyde forwards to after
// auditing (egress). Merges what used to be split across "AI Providers" (the
// upstream list) and the agent-endpoint block under "Labels".
//
// Providers are declared in config.yaml and are read-only here for everyone;
// this page is a transparent reference, not an editor.
export default function LlmRoutingPage() {
  return (
    <>
      <PageHeader
        title="LLM Providers"
        description="How agent traffic reaches each LLM through the gateway: agents call a Kyde URL (ingress), Kyde audits the request, then forwards it to the upstream provider (egress)."
      />

      <Alert className="mb-4 border-info/40 bg-info/5 text-foreground [&>svg]:text-info">
        <Network className="h-4 w-4" />
        <AlertDescription className="text-sm">
          Providers are declared in <code className="font-mono text-xs">config.yaml</code>{" "}
          under <code className="font-mono text-xs">upstreams:</code> — read-only here; to
          add or rename one, edit the file and restart the service. MCP tool routing (editable
          from the UI) lives under MCP Servers.
        </AlertDescription>
      </Alert>

      <AgentEndpoints />
    </>
  );
}
