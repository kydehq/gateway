// Thin fetch wrapper. Handles the two SPA-level auth bounces the
// backend signals via status codes:
//   401 → /login (or /setup if server says setup_required)
//   409 with body.error === "password_change_required" → /change-password
// In both cases we navigate and return a never-resolving promise so
// consumers don't see a transient error UI while the page tears down.

// API + auth paths are mounted at the origin root by nginx. Don't
// derive this from window.location.pathname — that string changes per
// route, so a deep-link load on /threats-alerts would send fetches to
// /threats-alerts/api/whoami, which the SPA fallback serves as HTML,
// blanking the page. If subpath mounting is ever needed, switch to
// import.meta.env.VITE_API_BASE.
export const API_BASE: string = "";

export interface FetchOptions extends RequestInit {
  signal?: AbortSignal;
}

export async function fetchJSON<T = unknown>(
  url: string,
  opts: FetchOptions = {},
): Promise<T> {
  const r = await fetch(API_BASE + url, {
    ...opts,
    headers: {
      Accept: "application/json",
      ...(opts.body && !(opts.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(opts.headers ?? {}),
    },
  });

  if (r.status === 401) {
    const body = await r.clone().json().catch(() => ({}) as { error?: string });
    const target = body?.error === "setup_required" ? "/setup" : "/login";
    window.location.assign(API_BASE + target);
    return new Promise<T>(() => {});
  }

  if (r.status === 409) {
    const body = await r.clone().json().catch(() => ({}) as { error?: string });
    if (body?.error === "password_change_required") {
      window.location.assign(API_BASE + "/change-password");
      return new Promise<T>(() => {});
    }
  }

  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.clone().json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      else if (body?.error) msg = String(body.error);
    } catch {
      /* body not JSON */
    }
    throw new Error(msg);
  }

  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// Download a binary or text response from a POST endpoint and trigger a
// browser save. Returns once the blob has been handed to the download
// anchor; rejects on any non-2xx so callers can surface a toast.
//
// `accept` is the expected response MIME type sent in the Accept header
// — the response Content-Type drives the actual file format the browser
// saves. Default 'application/pdf' for backwards-compat with the four
// 🛡 PDF export buttons.
export async function downloadFile(
  url: string,
  body: unknown,
  filename: string,
  accept = "application/pdf",
): Promise<void> {
  const r = await fetch(API_BASE + url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: accept },
    body: JSON.stringify(body ?? {}),
  });
  if (r.status === 401) {
    window.location.assign(API_BASE + "/login");
    return;
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.clone().json();
      if (j?.error) msg = String(j.error);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke after the click handler has a tick to consume the URL.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

// PDF-specific alias. New code can use downloadFile directly; this keeps
// the existing call sites working without churn.
export const downloadPdf = (url: string, body: unknown, filename: string) =>
  downloadFile(url, body, filename, "application/pdf");


export function qs(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? "?" + s : "";
}
