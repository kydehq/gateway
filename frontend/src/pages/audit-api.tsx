import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, Copy } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Curated documentation of the audit-relevant API surface. Distinct from
// FastAPI's auto-generated /docs because it strips down to what an
// auditor handing over compliance evidence actually needs — the auth
// model, the data shape, and the verification recipe.
//
// Kept hand-written rather than pointing at /openapi.json so we can
// annotate role gating and add cross-references to other dashboard pages.

interface Endpoint {
  method: string;
  path: string;
  summary: string;
  role: "viewer" | "admin" | "auditor" | "admin-or-auditor";
  body?: string;
  response?: string;
  notes?: string;
}

const ENDPOINTS: { group: string; items: Endpoint[] }[] = [
  {
    group: "Chain verification",
    items: [
      {
        method: "GET",
        path: "/api/verify",
        summary:
          "Walk the entire ledger and verify every entry's signature and prev_hash. " +
          "Each call also writes one row to verification_runs.",
        role: "viewer",
        response: "{ valid, entry_count, chain_breaks, signature_failures, errors[], fingerprint }",
        notes: "The fingerprint is the SHA-256 of the public key (hex, first 32 chars).",
      },
      {
        method: "GET",
        path: "/api/verification-runs",
        summary: "Newest-first history of /api/verify executions.",
        role: "viewer",
        response: "[{ run_id, run_at, total_entries, verified_entries, chain_breaks, signature_failures, status, error_sample[] }]",
        notes: "Drives the Verification History list on the Compliance page.",
      },
    ],
  },
  {
    group: "Evidence export",
    items: [
      {
        method: "POST",
        path: "/api/export/compliance-report",
        summary:
          "Generate the one-page Compliance summary PDF. " +
          "Signed; appends a Cryptographic verification block on the last page.",
        role: "viewer",
        response: "application/pdf",
      },
      {
        method: "POST",
        path: "/api/export/audit-log",
        summary: "Filtered audit-log entries as a signed PDF.",
        role: "viewer",
        body: "{ action?, upstream?, q?, window?, limit? }",
        response: "application/pdf",
      },
      {
        method: "POST",
        path: "/api/export/audit-log-csv",
        summary: "Same filter set as the PDF, returned as CSV.",
        role: "viewer",
        body: "{ action?, upstream?, q?, window?, limit? }",
        response: "text/csv",
      },
      {
        method: "POST",
        path: "/api/export/ledger-csv",
        summary:
          "Full ledger CSV dump for compliance handoff — metadata only " +
          "(no message bodies). Includes prev_hash / entry_hash / signature_b64.",
        role: "admin-or-auditor",
        body: "{ window? }",
        response: "text/csv",
        notes: "Use window='all' for a full archive.",
      },
      {
        method: "POST",
        path: "/api/export/chain-signatures",
        summary:
          "JSON archive designed for offline cryptographic verification. " +
          "Each entry carries the signable payload + signature; the file root " +
          "carries the PEM-encoded public key.",
        role: "admin-or-auditor",
        body: "{ window? }",
        response: "application/json",
        notes:
          "Verifier reconstructs canonical_bytes = json.dumps(signable, " +
          "sort_keys=True, separators=(',', ':')).encode('utf-8'). SHA-256(canon) " +
          "must equal entry_hash; verify_payload(signable, signature_b64) returns True.",
      },
      {
        method: "POST",
        path: "/api/export/compliance-evidence",
        summary: "Single-session or single-alert evidence PDF.",
        role: "viewer",
        body: "{ kind: 'session' | 'alert', id }",
        response: "application/pdf",
      },
      {
        method: "POST",
        path: "/api/export/incident-report",
        summary: "Per-chain incident report (Agent Chains export).",
        role: "viewer",
        body: "{ chain_label, status, incident_serial, steps[], notes }",
        response: "application/pdf",
      },
    ],
  },
  {
    group: "Read access",
    items: [
      {
        method: "GET",
        path: "/api/entries",
        summary: "Paginated audit log. Cursor pagination by ledger.seq.",
        role: "viewer",
        notes:
          "Query: limit, cursor, action, upstream, agent_id, session_id, q, window. " +
          "Returns items[] + next_cursor + has_more + total_count.",
      },
      {
        method: "GET",
        path: "/api/sessions",
        summary: "Paginated session summaries with derived status.",
        role: "viewer",
        notes:
          "Query: limit, cursor, window, has_alert, agent (multi), sort, status (multi). " +
          "Each item carries serial_id, agents[], status (blocked|observed|allowed).",
      },
      {
        method: "GET",
        path: "/api/sessions/{session_id}",
        summary: "Entries for one session with inline DLP alerts attached.",
        role: "viewer",
        notes: "why content is gated behind the auditor role.",
      },
      {
        method: "GET",
        path: "/api/dlp-alerts",
        summary: "All DLP alerts decorated with agent_id and serial_id.",
        role: "viewer",
      },
      {
        method: "GET",
        path: "/api/topology",
        summary: "Sankey aggregation: segment → agent → tool → upstream → model.",
        role: "viewer",
      },
      {
        method: "GET",
        path: "/api/topology/flow",
        summary: "Drill-down for one Sankey link — top agents + recent sessions.",
        role: "viewer",
        notes:
          "Query: source_layer, source_label, target_layer, target_label, window.",
      },
      {
        method: "GET",
        path: "/api/topology/agent/{agent_id}",
        summary: "Per-agent breakdown — tools, upstreams, models, sessions.",
        role: "viewer",
        notes: "Powers the /agents/:agent_id deep-link page.",
      },
      {
        method: "GET",
        path: "/api/topology/ip/{ip}",
        summary:
          "Per-IP breakdown — agents, tools, upstreams, models, sessions. " +
          "Triggers the lazy reverse-DNS resolver and returns the cached " +
          "hostname in the response.",
        role: "viewer",
        notes: "Powers the /hosts/:identifier deep-link page.",
      },
      {
        method: "GET",
        path: "/api/hosts/resolve",
        summary:
          "Bidirectional host lookup. ?identifier=<ip> returns the cached " +
          "hostname (triggers lazy resolve). ?identifier=<hostname> returns " +
          "the matching IPs sorted most-recently-seen first.",
        role: "viewer",
      },
      {
        method: "GET",
        path: "/api/host-labels",
        summary:
          "Hosts table for Settings → Host names. ?status=all|labeled|" +
          "unlabeled|recently_active, ?q=<search>.",
        role: "viewer",
      },
      {
        method: "GET",
        path: "/api/agents",
        summary:
          "Agent roster with display_name + activity (entries, sessions, first/last seen).",
        role: "viewer",
      },
      {
        method: "GET",
        path: "/api/token-analysis",
        summary:
          "Token aggregates by agent / model / upstream / hour. Window-scoped.",
        role: "viewer",
      },
    ],
  },
  {
    group: "Mutations",
    items: [
      {
        method: "PATCH",
        path: "/api/agents/{agent_id}",
        summary: "Set or clear an agent's human-readable display_name.",
        role: "admin",
        body: "{ display_name: string | null }",
      },
      {
        method: "POST",
        path: "/api/agents/{agent_id}/block",
        summary:
          "Add an agent to the proxy block list. Future requests carrying " +
          "this agent_id are rejected with HTTP 403 and a policy_block ledger row.",
        role: "admin",
        body: "{ reason? }",
      },
      {
        method: "DELETE",
        path: "/api/agents/{agent_id}/block",
        summary: "Remove an agent from the block list.",
        role: "admin",
      },
      {
        method: "POST",
        path: "/api/dlp-alerts/{alert_id}/transition",
        summary: "Lifecycle / disposition transition for a DLP alert.",
        role: "viewer",
        body: "{ to_status, disposition?, assignee_id?, note?, metadata? }",
        notes: "Used by the Threats & Alerts triage workflow including bulk actions.",
      },
      {
        method: "PUT",
        path: "/api/host-labels/{ip}",
        summary:
          "Set or update an admin hostname label for an IP. Admin labels " +
          "are never overwritten by the DNS resolver.",
        role: "admin",
        body: "{ hostname }",
      },
      {
        method: "DELETE",
        path: "/api/host-labels/{ip}",
        summary:
          "Clear an admin hostname label. DNS may repopulate on the next read.",
        role: "admin",
      },
      {
        method: "POST",
        path: "/api/host-labels/{ip}/refresh",
        summary:
          "Force a reverse-DNS refresh, bypassing TTL. Respects admin " +
          "precedence — never overwrites an admin label.",
        role: "admin",
      },
      {
        method: "POST",
        path: "/api/sessions/{session_id}/classify",
        summary:
          "Run the LLM-backed intent classifier for one session and cache " +
          "the result in session_intents.",
        role: "viewer",
        notes: "Returns 503 when INTENT_CLASSIFIER_URL env is unset.",
      },
    ],
  },
];

const ROLE_STYLE: Record<Endpoint["role"], string> = {
  viewer:             "bg-muted text-muted-foreground border-border",
  admin:              "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
  auditor:            "bg-primary/10 text-primary border-primary/20",
  "admin-or-auditor": "bg-primary/10 text-primary border-primary/20",
};
const ROLE_LABEL: Record<Endpoint["role"], string> = {
  viewer: "viewer+",
  admin: "admin",
  auditor: "auditor",
  "admin-or-auditor": "admin / auditor",
};
const METHOD_STYLE: Record<string, string> = {
  GET:    "bg-success/10 text-success border-success/20",
  POST:   "bg-primary/10 text-primary border-primary/20",
  PATCH:  "bg-sev-medium/10 text-sev-medium border-sev-medium/20",
  DELETE: "bg-sev-critical/10 text-sev-critical border-sev-critical/20",
};

function CopyableEndpoint({ method, path }: { method: string; path: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(`${method} ${path}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* ignore */
    }
  };
  return (
    <div className="flex items-center gap-2">
      <span className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-mono font-bold", METHOD_STYLE[method] ?? "")}>
        {method}
      </span>
      <code className="font-mono text-xs">{path}</code>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-6 px-1 text-muted-foreground"
        onClick={onCopy}
        title="Copy"
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      </Button>
    </div>
  );
}

export default function AuditApiPage() {
  return (
    <>
      <PageHeader
        title="Audit API"
        description="Read + verify endpoints for compliance handoff. Auth roles are noted per endpoint."
      />

      <div className="rounded-lg border bg-card p-5 mb-7 text-sm">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Authentication
        </p>
        <p className="text-muted-foreground mb-3">
          All endpoints require a valid session cookie. Roles:
          <strong className="text-foreground"> viewer+</strong> = any authenticated user,
          <strong className="text-foreground"> admin</strong> = admin-only,
          <strong className="text-foreground"> auditor</strong> = explicit auditor role (grants message-body access),
          <strong className="text-foreground"> admin / auditor</strong> = either role.
        </p>
        <p className="text-xs text-muted-foreground">
          The compliance-handoff exports (Ledger CSV, Chain Signatures JSON) are
          admin-or-auditor gated to match the Compliance page's route guard.
          See <Link to="/compliance" className="text-primary hover:underline">Compliance</Link> for the
          public-key fingerprint and ledger-integrity status.
        </p>
      </div>

      {ENDPOINTS.map((group) => (
        <section key={group.group} className="mb-8">
          <h2 className="text-sm font-semibold tracking-tight mb-3">{group.group}</h2>
          <div className="rounded-md border divide-y">
            {group.items.map((e) => (
              <div key={`${e.method} ${e.path}`} className="px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <CopyableEndpoint method={e.method} path={e.path} />
                  <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wide", ROLE_STYLE[e.role])}>
                    {ROLE_LABEL[e.role]}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground mb-2">{e.summary}</p>
                {e.body && (
                  <p className="text-[11px] text-muted-foreground">
                    <span className="font-mono text-[10px] uppercase tracking-wider">body</span>{" "}
                    <code className="font-mono">{e.body}</code>
                  </p>
                )}
                {e.response && (
                  <p className="text-[11px] text-muted-foreground">
                    <span className="font-mono text-[10px] uppercase tracking-wider">response</span>{" "}
                    <code className="font-mono">{e.response}</code>
                  </p>
                )}
                {e.notes && (
                  <p className="mt-2 text-[11px] text-muted-foreground/80 leading-relaxed">{e.notes}</p>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      <div className="rounded-lg border bg-card p-5 text-sm">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Offline verification recipe
        </p>
        <p className="text-muted-foreground mb-3">
          For Chain Signatures JSON exports, each entry's signature can be
          verified independently:
        </p>
        <pre className="overflow-x-auto rounded bg-muted p-3 font-mono text-[11px] leading-relaxed">
{`import base64, hashlib, json
from cryptography.hazmat.primitives import serialization

# 1. Load the public key from the export's root.
pub_pem = base64.b64decode(export["public_key_pem_b64"])
public_key = serialization.load_pem_public_key(pub_pem)

# 2. For each entry, reconstruct canonical_bytes the same way the
#    gateway does (sort_keys=True, separators=(',', ':'), UTF-8).
for e in export["entries"]:
    canon = json.dumps(e["signable"], sort_keys=True,
                       separators=(",", ":")).encode("utf-8")

    # 3. Check the hash.
    assert hashlib.sha256(canon).hexdigest() == e["entry_hash"]

    # 4. Verify the Ed25519 signature.
    sig = base64.b64decode(e["signature_b64"])
    public_key.verify(sig, canon)`}
        </pre>
      </div>
    </>
  );
}
