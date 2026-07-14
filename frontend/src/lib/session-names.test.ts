import { describe, it, expect } from "vitest";
import { classifyIntent, getSessionDisplayName } from "./session-names";

describe("classifyIntent", () => {
  it("matches keywords case-insensitively", () => {
    expect(classifyIntent("Update the CRM record")).toBe("CRM Query");
    expect(classifyIntent("please REFACTOR this module")).toBe("Code Refactor");
    expect(classifyIntent("debugging the worker")).toBe("Debug Session");
  });

  it("returns the first matching keyword in priority order", () => {
    // 'code' precedes 'query' in the keyword list, so a string with both
    // resolves to the earlier entry.
    expect(classifyIntent("code query")).toBe("Code Session");
  });

  it("falls back to 'Untitled Session' when nothing matches", () => {
    expect(classifyIntent("hello there")).toBe("Untitled Session");
    expect(classifyIntent("")).toBe("Untitled Session");
  });
});

describe("getSessionDisplayName", () => {
  it("maps a known backend LLM intent label to its human-readable name", () => {
    const name = getSessionDisplayName({
      session_id: "s1",
      agent_id: "agent:abcdef1234567890",
      intent: "code_review",
    });
    expect(name).toBe("abcdef12 · Code Review");
  });

  it("passes an unknown backend intent through verbatim", () => {
    const name = getSessionDisplayName({
      session_id: "s1",
      agent_id: "abcdef1234567890",
      intent: "bespoke_label",
    });
    expect(name).toBe("abcdef12 · bespoke_label");
  });

  it("falls back to the keyword classifier on first_message when no intent", () => {
    const name = getSessionDisplayName({
      session_id: "s1",
      agent_id: "abcdef1234567890",
      first_message: "help me debug this",
    });
    expect(name).toBe("abcdef12 · Debug Session");
  });

  it("labels a missing agent as 'Unknown Agent'", () => {
    const name = getSessionDisplayName({ session_id: "s1", intent: "research" });
    expect(name).toBe("Unknown Agent · Research");
  });

  it("appends a formatted timestamp when first_time is present", () => {
    const name = getSessionDisplayName({
      session_id: "s1",
      agent_id: "abcdef1234567890",
      intent: "research",
      first_time: "2026-04-21T11:40:10Z",
    });
    expect(name.startsWith("abcdef12 · Research · ")).toBe(true);
    // The trailing segment is locale/timezone-formatted; assert it is non-empty
    // rather than pinning an exact string that depends on the runner's TZ.
    expect(name.split(" · ")).toHaveLength(3);
    expect(name.split(" · ")[2].length).toBeGreaterThan(0);
  });
});
