// Render a host as "hostname (ip)" when a hostname is known, otherwise
// just the IP. Centralized so every page that surfaces an IP renders
// the same way — Network Map, Agent detail, Host page, Audit Log
// previews, Threats detail. Pure function, safe in any render context.
export function formatHost(
  ip: string | null | undefined,
  hostname?: string | null,
): string {
  if (!ip) return hostname ?? "";
  if (!hostname) return ip;
  return `${hostname} (${ip})`;
}
