export function truncate(s: string | null | undefined, n: number): string {
  if (!s) return "-";
  return s.length > n ? s.slice(0, n) + "..." : s;
}

export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return "-";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

export function fmtDate(dt: string | null | undefined): string {
  if (!dt) return "-";
  return dt;
}
