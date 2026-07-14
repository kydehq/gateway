import { describe, it, expect } from "vitest";
import { formatHost } from "./host-format";

describe("formatHost", () => {
  it("renders 'hostname (ip)' when both are known", () => {
    expect(formatHost("10.0.0.5", "web-01")).toBe("web-01 (10.0.0.5)");
  });

  it("renders the IP alone when no hostname is known", () => {
    expect(formatHost("10.0.0.5")).toBe("10.0.0.5");
    expect(formatHost("10.0.0.5", null)).toBe("10.0.0.5");
    expect(formatHost("10.0.0.5", "")).toBe("10.0.0.5");
  });

  it("falls back to the hostname when the IP is missing", () => {
    expect(formatHost(null, "web-01")).toBe("web-01");
    expect(formatHost(undefined, "web-01")).toBe("web-01");
    expect(formatHost("", "web-01")).toBe("web-01");
  });

  it("returns an empty string when nothing is known", () => {
    expect(formatHost(null)).toBe("");
    expect(formatHost(null, null)).toBe("");
    expect(formatHost("", "")).toBe("");
  });
});
