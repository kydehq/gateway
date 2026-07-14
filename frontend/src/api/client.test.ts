import { describe, it, expect } from "vitest";
import { qs } from "./client";

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
