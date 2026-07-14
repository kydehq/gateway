import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { formatAbsolute, RelativeTime } from "./relative-time";

describe("formatAbsolute", () => {
  it("returns a dash for nullish, empty, or unparseable input", () => {
    expect(formatAbsolute(null)).toBe("-");
    expect(formatAbsolute(undefined)).toBe("-");
    expect(formatAbsolute("")).toBe("-");
    expect(formatAbsolute("not-a-date")).toBe("-");
  });

  it("infers seconds vs milliseconds by magnitude to the same instant", () => {
    // 1.7e9 (seconds) and 1.7e12 (ms) denote the same moment.
    expect(formatAbsolute(1_700_000_000)).toBe(formatAbsolute(1_700_000_000_000));
  });

  it("parses a pure-numeric string through the same unix path", () => {
    expect(formatAbsolute("1700000000")).toBe(formatAbsolute(1_700_000_000));
  });

  it("parses a space-separated ISO-ish timestamp (treated as UTC)", () => {
    expect(formatAbsolute("2026-04-21 11:40:10")).not.toBe("-");
  });
});

describe("RelativeTime", () => {
  it("renders a dash when the value cannot be parsed", () => {
    render(<RelativeTime value={null} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("renders a relative, suffixed label for a time in the past", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    render(<RelativeTime value={fiveMinAgo} />);
    expect(screen.getByText(/ago$/)).toBeInTheDocument();
  });
});
