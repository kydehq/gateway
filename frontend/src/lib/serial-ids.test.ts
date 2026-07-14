import { describe, it, expect } from "vitest";
import {
  formatAlertId,
  formatChainId,
  formatSessionId,
  formatIncidentId,
  formatSeqId,
} from "./serial-ids";

describe("serial-id formatters", () => {
  it("zero-pads to 4 digits with the right prefix", () => {
    expect(formatAlertId(42)).toBe("ALT-0042");
    expect(formatChainId(1)).toBe("CHAIN-0001");
    expect(formatSessionId(7)).toBe("SES-0007");
    expect(formatIncidentId(123)).toBe("INC-0123");
  });

  it("does not truncate serials longer than the pad width", () => {
    expect(formatAlertId(12345)).toBe("ALT-12345");
    expect(formatChainId(1000000)).toBe("CHAIN-1000000");
  });

  it("accepts integer-like strings", () => {
    expect(formatAlertId("42")).toBe("ALT-0042");
    expect(formatSessionId("9")).toBe("SES-0009");
  });

  it("renders a visible sentinel for missing serials", () => {
    for (const missing of [null, undefined, ""] as const) {
      expect(formatAlertId(missing)).toBe("ALT-????");
      expect(formatChainId(missing)).toBe("CHAIN-????");
      expect(formatSessionId(missing)).toBe("SES-????");
      expect(formatIncidentId(missing)).toBe("INC-????");
    }
  });

  it("treats zero as a present value, not missing", () => {
    expect(formatAlertId(0)).toBe("ALT-0000");
  });
});

describe("formatSeqId", () => {
  it("renders SEQ-<n> without zero-padding", () => {
    expect(formatSeqId(5)).toBe("SEQ-5");
    expect(formatSeqId("17")).toBe("SEQ-17");
    expect(formatSeqId(0)).toBe("SEQ-0");
  });

  it("renders a short sentinel when the sequence is missing", () => {
    expect(formatSeqId(null)).toBe("SEQ-?");
    expect(formatSeqId(undefined)).toBe("SEQ-?");
    expect(formatSeqId("")).toBe("SEQ-?");
  });
});
