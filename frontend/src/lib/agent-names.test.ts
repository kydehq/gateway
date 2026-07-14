import { describe, it, expect } from "vitest";
import { getAgentDisplayName, getAgentShortName } from "./agent-names";

describe("getAgentDisplayName", () => {
  it("formats a raw string id as a Claude Code Agent with an 8-char hash", () => {
    expect(getAgentDisplayName("abcdef1234567890")).toBe(
      "Claude Code Agent (abcdef12)",
    );
  });

  it("strips a leading 'agent:' prefix before slicing the hash", () => {
    expect(getAgentDisplayName("agent:abcdef1234567890")).toBe(
      "Claude Code Agent (abcdef12)",
    );
  });

  it("prefers an explicit display_name on an object", () => {
    expect(
      getAgentDisplayName({ id: "abcdef1234", display_name: "Billing Bot" }),
    ).toBe("Billing Bot");
  });

  it("uses primary_tool in the label when no display_name is set", () => {
    expect(
      getAgentDisplayName({ id: "agent:abcdef1234", primary_tool: "Cursor" }),
    ).toBe("Cursor Agent (abcdef12)");
  });

  it("defaults the tool to 'Claude Code' when neither field is present", () => {
    expect(getAgentDisplayName({ id: "abcdef1234567890" })).toBe(
      "Claude Code Agent (abcdef12)",
    );
  });
});

describe("getAgentShortName", () => {
  it("leaves names of 32 chars or fewer intact", () => {
    // "Claude Code Agent (abcdef12)" is 28 chars.
    expect(getAgentShortName("abcdef1234567890")).toBe(
      "Claude Code Agent (abcdef12)",
    );
  });

  it("truncates long display names to 30 chars plus an ellipsis", () => {
    const long = "A".repeat(40);
    const short = getAgentShortName({ id: "x", display_name: long });
    expect(short).toBe("A".repeat(30) + "…");
    expect(short).toHaveLength(31);
  });
});
