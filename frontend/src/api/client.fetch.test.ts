import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { fetchJSON } from "./client";

// A real Response so .clone()/.json()/.ok behave exactly as in the browser.
const jsonRes = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  // fetchJSON calls window.location.assign for the auth bounces; jsdom's real
  // navigation is unimplemented, so replace location with a spyable stub.
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign },
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJSON — happy paths", () => {
  it("returns parsed JSON on 200", async () => {
    fetchMock.mockResolvedValue(jsonRes(200, { hello: "world" }));
    await expect(fetchJSON("/x")).resolves.toEqual({ hello: "world" });
  });

  it("returns undefined on 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(fetchJSON("/x")).resolves.toBeUndefined();
  });

  it("sends Accept always and Content-Type only when a (non-FormData) body is present", async () => {
    // Fresh Response per call — a single Response body can only be read once.
    fetchMock.mockImplementation(() => Promise.resolve(jsonRes(200, { ok: true })));

    await fetchJSON("/post", { method: "POST", body: JSON.stringify({ a: 1 }) });
    let headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Accept).toBe("application/json");
    expect(headers["Content-Type"]).toBe("application/json");

    fetchMock.mockClear();
    await fetchJSON("/get");
    headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Accept).toBe("application/json");
    expect(headers["Content-Type"]).toBeUndefined();
  });
});

describe("fetchJSON — auth bounces", () => {
  it("401 navigates to /login and never resolves", async () => {
    fetchMock.mockResolvedValue(jsonRes(401, {}));
    let settled = false;
    void fetchJSON("/x").then(
      () => (settled = true),
      () => (settled = true),
    );
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    // Confirm the promise stays pending (page is tearing down), not resolved.
    await Promise.resolve();
    expect(settled).toBe(false);
  });

  it("401 with setup_required navigates to /setup", async () => {
    fetchMock.mockResolvedValue(jsonRes(401, { error: "setup_required" }));
    void fetchJSON("/x");
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/setup"));
  });

  it("409 with password_change_required navigates to /change-password", async () => {
    fetchMock.mockResolvedValue(
      jsonRes(409, { error: "password_change_required" }),
    );
    void fetchJSON("/x");
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("/change-password"),
    );
  });

  it("409 without that error falls through and throws", async () => {
    fetchMock.mockResolvedValue(jsonRes(409, { error: "conflict" }));
    await expect(fetchJSON("/x")).rejects.toThrow("conflict");
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("fetchJSON — error extraction", () => {
  it("prefers `detail` from a JSON error body", async () => {
    fetchMock.mockResolvedValue(jsonRes(500, { detail: "boom" }));
    await expect(fetchJSON("/x")).rejects.toThrow("boom");
  });

  it("falls back to `error` when there is no detail", async () => {
    fetchMock.mockResolvedValue(jsonRes(422, { error: "bad input" }));
    await expect(fetchJSON("/x")).rejects.toThrow("bad input");
  });

  it("uses status + statusText when the body is not JSON", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>nope</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    await expect(fetchJSON("/x")).rejects.toThrow(/502/);
  });
});
