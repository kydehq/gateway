import { describe, it, expect } from "vitest";
import { filterByRange } from "./date-range-picker";

// new Date(year, monthIndex, day, hour): monthIndex is 0-based, so 3 = April.
// filterByRange formats both the range bounds and compares against key prefixes
// using local time consistently, so these assertions are timezone-independent.

describe("filterByRange", () => {
  it("returns the input untouched when the range has no start", () => {
    const obj = { "2026-04-21": 1 };
    expect(filterByRange(obj, undefined, 10)).toBe(obj);
    expect(filterByRange(obj, { from: undefined, to: undefined }, 10)).toBe(obj);
  });

  it("keeps only day-keyed entries within an inclusive [from, to] range", () => {
    const data = { "2026-04-20": 1, "2026-04-21": 2, "2026-04-22": 3 };
    const range = { from: new Date(2026, 3, 21), to: new Date(2026, 3, 22) };
    expect(filterByRange(data, range, 10)).toEqual({
      "2026-04-21": 2,
      "2026-04-22": 3,
    });
  });

  it("matches only the start day when no end is given", () => {
    const data = { "2026-04-20": 1, "2026-04-21": 2, "2026-04-22": 3 };
    const range = { from: new Date(2026, 3, 21) };
    expect(filterByRange(data, range, 10)).toEqual({ "2026-04-21": 2 });
  });

  it("supports hour-precision keys at keyLen 13", () => {
    const hours = {
      "2026-04-21T09": 1,
      "2026-04-21T10": 2,
      "2026-04-22T00": 3,
    };
    const range = {
      from: new Date(2026, 3, 21, 9),
      to: new Date(2026, 3, 21, 10),
    };
    expect(filterByRange(hours, range, 13)).toEqual({
      "2026-04-21T09": 1,
      "2026-04-21T10": 2,
    });
  });
});
