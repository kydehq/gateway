import { describe, it, expect } from "vitest";
import { truncate, fmtTokens, fmtDate } from "./format";

describe("truncate", () => {
  it("returns a dash for falsy input", () => {
    expect(truncate(null, 10)).toBe("-");
    expect(truncate(undefined, 10)).toBe("-");
    expect(truncate("", 10)).toBe("-");
  });

  it("leaves strings at or under the limit untouched", () => {
    expect(truncate("hello", 10)).toBe("hello");
    expect(truncate("exactlyten", 10)).toBe("exactlyten"); // length === n
  });

  it("truncates and appends an ellipsis when over the limit", () => {
    expect(truncate("hello world", 5)).toBe("hello...");
    expect(truncate("abcdef", 3)).toBe("abc...");
  });
});

describe("fmtTokens", () => {
  it("renders small counts verbatim", () => {
    expect(fmtTokens(0)).toBe("0");
    expect(fmtTokens(999)).toBe("999");
  });

  it("switches to K at 1,000 with one decimal", () => {
    expect(fmtTokens(1000)).toBe("1.0K");
    expect(fmtTokens(1500)).toBe("1.5K");
    expect(fmtTokens(999_999)).toBe("1000.0K");
  });

  it("switches to M at 1,000,000 with one decimal", () => {
    expect(fmtTokens(1_000_000)).toBe("1.0M");
    expect(fmtTokens(2_500_000)).toBe("2.5M");
  });

  it("returns a dash for non-finite input", () => {
    expect(fmtTokens(Number.NaN)).toBe("-");
    expect(fmtTokens(Number.POSITIVE_INFINITY)).toBe("-");
  });
});

describe("fmtDate", () => {
  it("passes through a present value and dashes a missing one", () => {
    expect(fmtDate("2026-04-21")).toBe("2026-04-21");
    expect(fmtDate(null)).toBe("-");
    expect(fmtDate(undefined)).toBe("-");
  });
});
