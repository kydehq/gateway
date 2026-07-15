import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { downloadFile, downloadPdf, fetchJSON, qs } from "./client";

const fetchMock = vi.fn();
const assign = vi.fn();

function jsonResponse(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  assign.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  // jsdom's location.assign throws "Not implemented"; shadow the whole
  // location object so the auth bounces are observable.
  vi.stubGlobal("location", { ...window.location, assign });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJSON", () => {
  it("returns the parsed body and sends Accept + Content-Type headers", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: 1 }));
    const result = await fetchJSON("/api/thing", {
      method: "POST",
      body: JSON.stringify({ a: 1 }),
    });
    expect(result).toEqual({ ok: 1 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/thing");
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
    });
  });

  it("omits Content-Type for GETs and FormData bodies", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));
    await fetchJSON("/api/thing");
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty("Content-Type");

    fetchMock.mockResolvedValue(jsonResponse({}));
    await fetchJSON("/api/upload", { method: "POST", body: new FormData() });
    expect(fetchMock.mock.calls[1][1].headers).not.toHaveProperty("Content-Type");
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(fetchJSON("/api/thing")).resolves.toBeUndefined();
  });

  it("bounces to /login on 401 and never resolves", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "unauthorized" }, { status: 401 }));
    void fetchJSON("/api/thing");
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("bounces to /setup when the 401 says setup_required", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "setup_required" }, { status: 401 }));
    void fetchJSON("/api/thing");
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/setup"));
  });

  it("bounces to /change-password on a 409 password_change_required", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "password_change_required" }, { status: 409 }),
    );
    void fetchJSON("/api/thing");
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/change-password"));
  });

  it("throws the detail string from an error body", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "name already taken" }, { status: 409, statusText: "Conflict" }),
    );
    await expect(fetchJSON("/api/thing")).rejects.toThrow("name already taken");
  });

  it("stringifies a structured detail and falls back to error / statusText", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: { loc: ["body"] } }, { status: 422, statusText: "Unprocessable" }),
    );
    await expect(fetchJSON("/api/thing")).rejects.toThrow('{"loc":["body"]}');

    fetchMock.mockResolvedValue(jsonResponse({ error: "nope" }, { status: 403, statusText: "Forbidden" }));
    await expect(fetchJSON("/api/thing")).rejects.toThrow("nope");

    fetchMock.mockResolvedValue(
      new Response("<html>", { status: 500, statusText: "Internal Server Error" }),
    );
    await expect(fetchJSON("/api/thing")).rejects.toThrow("500 Internal Server Error");
  });
});

describe("downloadFile / downloadPdf", () => {
  let click: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:mock"),
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      configurable: true,
    });
    click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("POSTs the body and hands the blob to a download anchor", async () => {
    fetchMock.mockResolvedValue(new Response(new Blob(["%PDF"]), { status: 200 }));
    await downloadFile("/api/export/x", { window: "30d" }, "x.csv", "text/csv");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/export/x");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Accept: "text/csv" });
    expect(init.body).toBe(JSON.stringify({ window: "30d" }));
    expect(click).toHaveBeenCalledOnce();
  });

  it("defaults the Accept header to application/pdf via downloadPdf", async () => {
    fetchMock.mockResolvedValue(new Response(new Blob(["%PDF"]), { status: 200 }));
    await downloadPdf("/api/export/x", null, "x.pdf");
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      Accept: "application/pdf",
    });
    // null body serializes to an empty object.
    expect(fetchMock.mock.calls[0][1].body).toBe("{}");
  });

  it("bounces to /login on 401 without throwing", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    await expect(downloadFile("/api/export/x", {}, "x.pdf")).resolves.toBeUndefined();
    expect(assign).toHaveBeenCalledWith("/login");
    expect(click).not.toHaveBeenCalled();
  });

  it("throws the JSON error message on failure, or statusText for non-JSON", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "export too large" }, { status: 413, statusText: "Too Large" }),
    );
    await expect(downloadFile("/api/export/x", {}, "x.pdf")).rejects.toThrow(
      "export too large",
    );

    fetchMock.mockResolvedValue(
      new Response("boom", { status: 503, statusText: "Service Unavailable" }),
    );
    await expect(downloadFile("/api/export/x", {}, "x.pdf")).rejects.toThrow(
      "503 Service Unavailable",
    );
  });
});

describe("qs", () => {
  it("returns an empty string when there are no usable params", () => {
    expect(qs({})).toBe("");
    expect(qs({ a: undefined, b: null, c: "" })).toBe("");
  });

  it("prefixes a leading '?' and serializes present values", () => {
    expect(qs({ a: "1" })).toBe("?a=1");
  });

  it("skips undefined, null, and empty-string values but keeps 0 and false", () => {
    expect(qs({ a: 1, b: undefined, c: null, d: "", e: 0, f: false })).toBe(
      "?a=1&e=0&f=false",
    );
  });

  it("stringifies numbers and booleans", () => {
    expect(qs({ limit: 50, active: true })).toBe("?limit=50&active=true");
  });

  it("url-encodes keys and values", () => {
    expect(qs({ q: "a b&c" })).toBe("?q=a+b%26c");
  });
});
