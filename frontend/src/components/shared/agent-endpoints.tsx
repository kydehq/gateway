import { useMemo } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { CopyButton } from "./copy-button";
import { useConfiguration, useSettings } from "@/api/queries";
import type { SettingEntry, UpstreamEntry } from "@/api/types";

// Per-upstream SDK env-var name. Kept minimal — unknown upstreams fall
// back to <NAME>_BASE_URL, which every decent SDK understands (and the
// rest can be adapted in a couple of lines).
const ENV_VAR_NAME: Record<string, string> = {
  openai:    "OPENAI_BASE_URL",
  anthropic: "ANTHROPIC_BASE_URL",
  claude:    "ANTHROPIC_BASE_URL",
  gemini:    "GEMINI_BASE_URL",
  copilot:   "COPILOT_BASE_URL",
  ollama:    "OLLAMA_HOST",
};

function envVarFor(name: string): string {
  return ENV_VAR_NAME[name.toLowerCase()] ?? `${name.toUpperCase()}_BASE_URL`;
}

function getSetting(settings: SettingEntry[] | undefined, key: string): string {
  if (!settings) return "";
  const hit = settings.find((s) => s.key === key);
  if (!hit || hit.value == null) return "";
  return String(hit.value);
}

function buildBase(protocol: string, hostname: string, port: string): string {
  const proto = protocol || window.location.protocol.replace(/:$/, "") || "http";
  const host = hostname || window.location.hostname || "localhost";
  const p = port || window.location.port || "";
  const defaultPort = (proto === "http" && p === "80") || (proto === "https" && p === "443");
  const hostport = p && !defaultPort ? `${host}:${p}` : host;
  return `${proto}://${hostport}`;
}

function buildAgentUrl(base: string, upstream: UpstreamEntry): string {
  // Every native upstream client expects a `/v1` suffix. The proxy
  // strips/re-adds /v1 based on the upstream's own api_prefix, but the
  // external URL the agent types is always `/<name>/v1`.
  return `${base}/${upstream.name}/v1`;
}

export function AgentEndpoints() {
  const { data: config, isLoading: cfgLoading } = useConfiguration();
  const { data: settings, isLoading: sLoading } = useSettings();

  const base = useMemo(
    () =>
      buildBase(
        getSetting(settings, "PUBLIC_PROTOCOL"),
        getSetting(settings, "PUBLIC_HOSTNAME"),
        getSetting(settings, "PUBLIC_PORT"),
      ),
    [settings],
  );

  if (cfgLoading || sLoading || !config) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
      </div>
    );
  }

  if (config.configured_upstreams.length === 0) {
    return <p className="text-sm text-muted-foreground">No upstreams configured.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-xs">
        <span className="text-muted-foreground">base</span>
        <code className="flex-1 break-all">{base}</code>
        <CopyButton value={base} label="base" />
      </div>

      {config.configured_upstreams.map((u) => {
        const url = buildAgentUrl(base, u);
        const envLine = `${envVarFor(u.name)}=${url}`;
        const egress = `${u.base}${u.api_prefix || ""}`;
        return (
          <div key={u.name} className="rounded-md border border-border bg-card p-4">
            <div className="mb-3 flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-primary" />
              <span className="font-mono text-sm font-semibold">{u.name}</span>
            </div>

            {/* Ingress — the gateway URL agents point their SDK at. */}
            <div className="mb-2 flex items-center gap-1">
              <div className="w-24 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                Agents call
              </div>
              <code className="flex-1 break-all font-mono text-xs">{url}</code>
              <CopyButton value={url} label="URL" />
            </div>

            <div className="mb-2 flex items-center gap-1">
              <div className="w-24 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                Env var
              </div>
              <code className="flex-1 break-all font-mono text-xs text-muted-foreground">
                {envLine}
              </code>
              <CopyButton value={envLine} label="env line" />
            </div>

            {/* Egress — where Kyde forwards the request after auditing it. */}
            <div className="flex items-center gap-1">
              <div className="w-24 shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                Kyde routes to
              </div>
              <code className="flex-1 break-all font-mono text-xs text-muted-foreground">
                {egress}
              </code>
            </div>
          </div>
        );
      })}
    </div>
  );
}
