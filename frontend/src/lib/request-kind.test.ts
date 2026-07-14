import { describe, it, expect } from "vitest";
import { describeKind, hasChatBody, synthesise } from "./request-kind";

describe("describeKind", () => {
  it("returns the descriptor for a known kind", () => {
    const d = describeKind("policy_block");
    expect(d.chip).toBe("BLOCKED");
    expect(d.tone).toBe("danger");
    expect(d.reason).toBe("request rejected by policy and never sent upstream");
  });

  it("plain chat has no synthesis reason", () => {
    expect(describeKind("chat").reason).toBeNull();
  });

  it("falls back to the 'unknown' descriptor for null/undefined/unrecognized", () => {
    const unknown = describeKind("unknown");
    expect(describeKind(null)).toEqual(unknown);
    expect(describeKind(undefined)).toEqual(unknown);
    // Unrecognized string (cast through unknown) also falls back.
    expect(describeKind("nonsense" as unknown as Parameters<typeof describeKind>[0])).toEqual(
      unknown,
    );
  });
});

describe("hasChatBody", () => {
  it("is true only for chat / nullish kinds", () => {
    expect(hasChatBody("chat")).toBe(true);
    expect(hasChatBody(null)).toBe(true);
    expect(hasChatBody(undefined)).toBe(true);
  });

  it("is false for non-chat kinds", () => {
    expect(hasChatBody("embedding")).toBe(false);
    expect(hasChatBody("chat_tool_only")).toBe(false);
    expect(hasChatBody("policy_block")).toBe(false);
  });
});

describe("synthesise", () => {
  it("returns an empty string for plain chat with no provenance", () => {
    expect(synthesise("chat", {})).toBe("");
  });

  it("joins the kind reason with model / upstream / token provenance", () => {
    const line = synthesise("embedding", {
      model: "text-embedding-3",
      upstream: "openai",
      promptTokens: 150,
      completionTokens: 0,
    });
    expect(line).toBe(
      "vector embedding call · text-embedding-3 · openai · 150 → 0 tokens",
    );
  });

  it("surfaces the first tool name on tool-only turns", () => {
    const line = synthesise("chat_tool_only", {
      firstTool: "execute_code",
      toolCount: 3,
      model: "claude-sonnet-4-6",
    });
    expect(line).toBe(
      "assistant replied with tool calls only — no text content · first tool: execute_code (+2 more) · claude-sonnet-4-6",
    );
  });

  it("omits the '(+N more)' suffix for a single tool call", () => {
    const line = synthesise("chat_tool_only", {
      firstTool: "read_file",
      toolCount: 1,
    });
    expect(line).toContain("first tool: read_file");
    expect(line).not.toContain("more)");
  });

  it("ignores a placeholder first tool of '-'", () => {
    const line = synthesise("chat_tool_only", { firstTool: "-" });
    expect(line).toBe("assistant replied with tool calls only — no text content");
  });

  it("emits a token segment when only one of prompt/completion is given", () => {
    expect(synthesise("chat", { promptTokens: 10 })).toBe("10 → 0 tokens");
    expect(synthesise("chat", { completionTokens: 5 })).toBe("0 → 5 tokens");
  });
});
